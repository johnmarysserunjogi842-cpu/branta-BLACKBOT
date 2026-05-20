// ============================================================
// core/data_fetcher.cpp  —  INSTITUTION-GRADE ULTRA-LOW-LATENCY
// ============================================================

#include <atomic>
#include <vector>
#include <string>
#include <cstdint>
#include <cmath>
#include <chrono>
#include <algorithm>
#include <unordered_map>
#include <memory>
#include <mutex>

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

namespace py = pybind11;

// ============================================================
// TUNABLE LIMITS
// ============================================================

constexpr size_t MAX_TICKS        = 1 << 20;   // 1M
constexpr size_t MAX_CANDLES      = 4096;      // power of 2 (ring)
constexpr int64_t STALE_FEED_MS   = 3000;

// ============================================================
// CONFIG
// ============================================================

struct InstrumentConfig {
    std::string symbol;
    double spread_limit = 5.0;
    bool session_filter = false;
};

// ============================================================
// DATA STRUCTURES
// ============================================================

struct Tick {
    int64_t ts;
    double bid;
    double ask;
    double vol;
};

struct Candle {
    int64_t ts;
    double o, h, l, c, v;
};

// ============================================================
// LOCK-FREE SPSC RING BUFFER
// ============================================================

template<typename T, size_t N>
class RingBuffer {
public:
    RingBuffer() : head_(0), tail_(0) {}

    inline bool push(const T& item) noexcept {
        size_t h = head_.load(std::memory_order_relaxed);
        size_t next = (h + 1) & (N - 1);

        if (next == tail_.load(std::memory_order_acquire))
            return false;

        buffer_[h] = item;
        head_.store(next, std::memory_order_release);
        return true;
    }

    inline bool pop(T& out) noexcept {
        size_t t = tail_.load(std::memory_order_relaxed);

        if (t == head_.load(std::memory_order_acquire))
            return false;

        out = buffer_[t];
        tail_.store((t + 1) & (N - 1), std::memory_order_release);
        return true;
    }

private:
    alignas(64) T buffer_[N];
    std::atomic<size_t> head_;
    std::atomic<size_t> tail_;
};

// ============================================================
// FIXED-SIZE CANDLE RING (NO ERASE COST)
// ============================================================

class CandleRing {
public:
    CandleRing() : index_(0), size_(0) {}

    inline void push(const Candle& c) noexcept {
        buffer_[index_] = c;
        index_ = (index_ + 1) & (MAX_CANDLES - 1);
        if (size_ < MAX_CANDLES) size_++;
    }

    inline size_t size() const noexcept { return size_; }

    inline const Candle& at(size_t i) const noexcept {
        return buffer_[(index_ - size_ + i) & (MAX_CANDLES - 1)];
    }

private:
    alignas(64) Candle buffer_[MAX_CANDLES];
    size_t index_;
    size_t size_;
};

// ============================================================
// DATA FETCHER
// ============================================================

class DataFetcher {
public:
    explicit DataFetcher(const InstrumentConfig& cfg)
        : symbol_(cfg.symbol),
          spread_limit_(cfg.spread_limit),
          session_filter_(cfg.session_filter),
          last_tick_ts_(0),
          current_minute_(-1),
          spread_ema_(0.0)
    {}

    // --------------------------------------------------------
    // HOT PATH
    // --------------------------------------------------------

    inline void on_tick(int64_t ts, double bid, double ask, double vol) noexcept {

        if (!std::isfinite(bid) || !std::isfinite(ask)) return;
        if (bid <= 0 || ask <= 0 || ask < bid) return;

        double spread = ask - bid;
        if (spread > spread_limit_) return;

        if (session_filter_ && !in_session(ts)) return;

        last_tick_ts_.store(ts, std::memory_order_relaxed);
        update_spread(spread);

        Tick t{ts, bid, ask, vol};
        tick_buffer_.push(t);

        build_1m(t);
    }

    // --------------------------------------------------------

    bool is_feed_stale() const noexcept {
        return (now_ms() - last_tick_ts_.load()) > STALE_FEED_MS;
    }

    std::string get_symbol() const { return symbol_; }

    // --------------------------------------------------------
    // SNAPSHOTS (NO ERASE COST)
    // --------------------------------------------------------

    py::array_t<double> snapshot_1m_np() const {
        return to_numpy(c1m_);
    }

    py::array_t<double> snapshot_5m_np() const {
        return to_numpy(c5m_);
    }

    py::array_t<double> snapshot_15m_np() const {
        return to_numpy(c15m_);
    }

    py::array_t<double> snapshot_1h_np() const {
        return to_numpy(c1h_);
    }

private:
    // ========================================================
    // INTERNAL STATE
    // ========================================================

    std::string symbol_;
    double spread_limit_;
    bool session_filter_;

    RingBuffer<Tick, MAX_TICKS> tick_buffer_;

    CandleRing c1m_;
    CandleRing c5m_;
    CandleRing c15m_;
    CandleRing c1h_;

    Candle current_;
    int64_t current_minute_;

    std::atomic<int64_t> last_tick_ts_;
    double spread_ema_;
    static constexpr double SPREAD_ALPHA = 0.02;

    // ========================================================
    // TIME
    // ========================================================

    static inline int64_t now_ms() noexcept {
        using namespace std::chrono;
        return duration_cast<milliseconds>(
            system_clock::now().time_since_epoch()
        ).count();
    }

    inline bool in_session(int64_t ts) const noexcept {
        int hour = (ts / 3600000) % 24;
        return hour >= 0 && hour <= 23;
    }

    inline void update_spread(double s) noexcept {
        spread_ema_ = (spread_ema_ == 0.0)
            ? s
            : (SPREAD_ALPHA * s + (1.0 - SPREAD_ALPHA) * spread_ema_);
    }

    static inline double mid(const Tick& t) noexcept {
        return 0.5 * (t.bid + t.ask);
    }

    // ========================================================
    // 1M BUILDER
    // ========================================================

    void build_1m(const Tick& t) noexcept {

        int64_t minute = t.ts / 60000;

        if (current_minute_ == -1) {
            start_candle(t, minute);
            return;
        }

        if (minute != current_minute_) {
            finalize_1m();
            start_candle(t, minute);
        } else {
            update_candle(t);
        }
    }

    inline void start_candle(const Tick& t, int64_t minute) noexcept {
        current_minute_ = minute;
        current_.ts = minute * 60000;
        double p = mid(t);
        current_.o = current_.h = current_.l = current_.c = p;
        current_.v = t.vol;
    }

    inline void update_candle(const Tick& t) noexcept {
        double p = mid(t);
        current_.h = std::max(current_.h, p);
        current_.l = std::min(current_.l, p);
        current_.c = p;
        current_.v += t.vol;
    }

    void finalize_1m() noexcept {
        c1m_.push(current_);
        aggregate();
    }

    // ========================================================
    // AGGREGATION (DETERMINISTIC)
    // ========================================================

    void aggregate() noexcept {
        aggregate_tf(5,  c5m_);
        aggregate_tf(15, c15m_);
        aggregate_tf(60, c1h_);
    }

    void aggregate_tf(int factor, CandleRing& dst) noexcept {

        if (c1m_.size() < (size_t)factor) return;

        const Candle& last = c1m_.at(c1m_.size() - 1);
        if ((last.ts / 60000) % factor != 0) return;

        Candle agg{};
        agg.ts = last.ts - (factor - 1) * 60000;
        agg.o = c1m_.at(c1m_.size() - factor).o;
        agg.h = last.h;
        agg.l = last.l;
        agg.c = last.c;
        agg.v = 0;

        for (int i = factor; i > 0; --i) {
            const Candle& c = c1m_.at(c1m_.size() - i);
            agg.h = std::max(agg.h, c.h);
            agg.l = std::min(agg.l, c.l);
            agg.v += c.v;
        }

        dst.push(agg);
    }

    // ========================================================
    // NUMPY SNAPSHOT
    // ========================================================

    py::array_t<double> to_numpy(const CandleRing& ring) const {

        size_t n = ring.size();
        py::array_t<double> arr({n, 6});
        auto m = arr.mutable_unchecked<2>();

        for (size_t i = 0; i < n; ++i) {
            const Candle& c = ring.at(i);
            m(i,0) = (double)c.ts;
            m(i,1) = c.o;
            m(i,2) = c.h;
            m(i,3) = c.l;
            m(i,4) = c.c;
            m(i,5) = c.v;
        }

        return arr;
    }
};

// ============================================================
// MULTI-SYMBOL ENGINE
// ============================================================

class MarketEngine {
public:
    void add_instrument(const InstrumentConfig& cfg) {
        std::lock_guard<std::mutex> lg(mu_);
        engines_[cfg.symbol] =
            std::make_shared<DataFetcher>(cfg);
    }

    std::shared_ptr<DataFetcher> get(const std::string& symbol) {
        std::lock_guard<std::mutex> lg(mu_);
        return engines_.at(symbol);
    }

private:
    std::unordered_map<std::string,
        std::shared_ptr<DataFetcher>> engines_;
    std::mutex mu_;
};

// ============================================================
// PYBIND11
// ============================================================

PYBIND11_MODULE(market_core, m) {

    py::class_<InstrumentConfig>(m, "InstrumentConfig")
        .def(py::init<>())
        .def_readwrite("symbol", &InstrumentConfig::symbol)
        .def_readwrite("spread_limit", &InstrumentConfig::spread_limit)
        .def_readwrite("session_filter", &InstrumentConfig::session_filter);

    py::class_<DataFetcher,
        std::shared_ptr<DataFetcher>>(m, "DataFetcher")
        .def("on_tick", &DataFetcher::on_tick)
        .def("is_feed_stale", &DataFetcher::is_feed_stale)
        .def("snapshot_1m_np",  &DataFetcher::snapshot_1m_np)
        .def("snapshot_5m_np",  &DataFetcher::snapshot_5m_np)
        .def("snapshot_15m_np", &DataFetcher::snapshot_15m_np)
        .def("snapshot_1h_np",  &DataFetcher::snapshot_1h_np)
        .def("get_symbol", &DataFetcher::get_symbol);

    py::class_<MarketEngine,
        std::shared_ptr<MarketEngine>>(m, "MarketEngine")
        .def(py::init<>())
        .def("add_instrument", &MarketEngine::add_instrument)
        .def("get", &MarketEngine::get);
}
