// ============================================================
// INSTITUTION-GRADE TRADE EXECUTOR (PRODUCTION SAFE)
// ============================================================

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <thread>
#include <mutex>
#include <fstream>
#include <iostream>

#include <boost/lockfree/queue.hpp>

// ============================================================
// CONSTANTS
// ============================================================

constexpr size_t SYMBOL_MAX_LEN  = 16;
constexpr size_t ACCOUNT_MAX_LEN = 32;
constexpr size_t QUEUE_CAPACITY  = 4096;

// ============================================================
// TYPES
// ============================================================

enum class Side { BUY, SELL };
enum class OrderType { MARKET, LIMIT };

enum class RejectReason {
    NONE,
    DUPLICATE_INTENT,
    SAFETY_BLOCK,
    BROKER_REJECT,
    MAX_POSITIONS,
    MARKET_CLOSED,
    SPREAD_TOO_WIDE,
    CIRCUIT_BREAKER,
};

// ------------------------------------------------------------
// SAFE POD INTENT (LOCKFREE SAFE)
// ------------------------------------------------------------

struct TradeIntent {
    uint64_t intent_id;
    char symbol[SYMBOL_MAX_LEN];
    Side side;
    double volume;
    double sl;
    double tp;
    char account[ACCOUNT_MAX_LEN];
    uint64_t ts_ns;
};

struct ExecutionDecision {
    bool allow_trade;
    int max_positions;
    uint64_t intent_id;
};

struct Fill {
    double price;
    double volume;
    uint64_t ts_ns;
};

struct ExecutionState {
    uint64_t intent_id;
    std::string symbol;
    std::string broker_order_id;
    double requested_volume;
    double filled_volume = 0.0;
    std::vector<Fill> fills;
    bool complete = false;
};

// ============================================================
// BROKER BRIDGE (EXTERNAL)
// ============================================================

struct BrokerAck {
    bool accepted;
    std::string order_id;
};

struct BrokerFill {
    std::string order_id;
    double price;
    double volume;
    bool is_final;
};

class BrokerBridge {
public:
    BrokerAck place_market_order(
        const std::string& account,
        const std::string& symbol,
        Side side,
        double volume,
        double sl,
        double tp,
        uint64_t intent_id
    );

    std::vector<BrokerFill> poll_fills();
    bool market_open(const std::string& symbol);
    double current_spread(const std::string& symbol);
    int open_positions(const std::string& account, const std::string& symbol);
};

// ============================================================
// FEEDBACK EMITTER (PYTHON BOUNDARY)
// ============================================================

class FeedbackEmitter {
public:
    void emit_execution_start(uint64_t intent_id, const std::string& symbol);
    void emit_partial_fill(uint64_t intent_id, double price, double volume);
    void emit_execution_complete(uint64_t intent_id, double avg_price);
    void emit_reject(uint64_t intent_id, RejectReason reason);
};

// ============================================================
// TRADE EXECUTOR
// ============================================================

class TradeExecutor {
public:

    TradeExecutor(
        BrokerBridge* broker,
        FeedbackEmitter* feedback
    )
        : broker_(broker),
          feedback_(feedback),
          running_(true),
          intent_queue_(QUEUE_CAPACITY)
    {
        load_idempotency_state();

        worker_ = std::thread(&TradeExecutor::execution_loop, this);
        reconciler_ = std::thread(&TradeExecutor::reconciliation_loop, this);
    }

    ~TradeExecutor() {
        shutdown();
    }

    void shutdown() {
        bool expected = true;
        if (!running_.compare_exchange_strong(expected, false))
            return;

        if (worker_.joinable()) worker_.join();
        if (reconciler_.joinable()) reconciler_.join();

        persist_idempotency_state();
    }

    // --------------------------------------------------------
    // PUBLIC SUBMIT API (FAST PATH)
    // --------------------------------------------------------

    void submit(
        const ExecutionDecision& decision,
        const TradeIntent& intent
    ) {
        if (!decision.allow_trade)
            return;

        // fast duplicate pre-check (non-locking best effort)
        {
            std::lock_guard<std::mutex> lg(state_mutex_);
            if (seen_intents_.count(intent.intent_id))
                return;
        }

        if (!intent_queue_.push(intent)) {
            feedback_->emit_reject(intent.intent_id, RejectReason::SAFETY_BLOCK);
        }
    }

private:

    // ========================================================
    // EXECUTION LOOP (HOT PATH)
    // ========================================================

    void execution_loop() {
        while (running_) {

            TradeIntent intent;

            if (!intent_queue_.pop(intent)) {
                std::this_thread::sleep_for(std::chrono::microseconds(10));
                continue;
            }

            execute(intent);
        }
    }

    void execute(const TradeIntent& intent) {

        const std::string symbol(intent.symbol);
        const std::string account(intent.account);

        // HARD IDEMPOTENCY
        {
            std::lock_guard<std::mutex> lg(state_mutex_);

            if (seen_intents_.count(intent.intent_id)) {
                feedback_->emit_reject(intent.intent_id, RejectReason::DUPLICATE_INTENT);
                return;
            }

            seen_intents_.insert(intent.intent_id);
            append_idempotency_wal(intent.intent_id);
        }

        // SAFETY CHECKS
        if (!broker_->market_open(symbol)) {
            feedback_->emit_reject(intent.intent_id, RejectReason::MARKET_CLOSED);
            return;
        }

        if (broker_->current_spread(symbol) > max_spread_) {
            feedback_->emit_reject(intent.intent_id, RejectReason::SPREAD_TOO_WIDE);
            return;
        }

        int open_pos = broker_->open_positions(account, symbol);
        if (open_pos >= max_positions_per_symbol_) {
            feedback_->emit_reject(intent.intent_id, RejectReason::MAX_POSITIONS);
            return;
        }

        feedback_->emit_execution_start(intent.intent_id, symbol);

        BrokerAck ack = broker_->place_market_order(
            account,
            symbol,
            intent.side,
            intent.volume,
            intent.sl,
            intent.tp,
            intent.intent_id
        );

        if (!ack.accepted) {
            feedback_->emit_reject(intent.intent_id, RejectReason::BROKER_REJECT);
            return;
        }

        {
            std::lock_guard<std::mutex> lg(state_mutex_);
            executions_[ack.order_id] = ExecutionState{
                intent.intent_id,
                symbol,
                ack.order_id,
                intent.volume
            };
        }
    }

    // ========================================================
    // RECONCILIATION LOOP
    // ========================================================

    void reconciliation_loop() {

        while (running_) {

            auto fills = broker_->poll_fills();

            for (const auto& f : fills)
                handle_fill(f);

            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }

    void handle_fill(const BrokerFill& fill) {

        std::lock_guard<std::mutex> lg(state_mutex_);

        auto it = executions_.find(fill.order_id);
        if (it == executions_.end())
            return;

        auto& ex = it->second;

        ex.filled_volume += fill.volume;
        ex.fills.push_back({fill.price, fill.volume, now_ns()});

        feedback_->emit_partial_fill(ex.intent_id, fill.price, fill.volume);

        if (fill.is_final || ex.filled_volume >= ex.requested_volume) {

            ex.complete = true;

            double vwap = 0.0;
            for (const auto& f : ex.fills)
                vwap += f.price * f.volume;

            vwap /= ex.filled_volume;

            feedback_->emit_execution_complete(ex.intent_id, vwap);
        }
    }

    // ========================================================
    // IDEMPOTENCY WAL
    // ========================================================

    void append_idempotency_wal(uint64_t id) {
        wal_ << id << "\n";
        wal_.flush();
    }

    void load_idempotency_state() {
        std::ifstream in("executor_idempotency.wal");
        uint64_t id;
        while (in >> id)
            seen_intents_.insert(id);

        wal_.open("executor_idempotency.wal", std::ios::app);
    }

    void persist_idempotency_state() {
        wal_.flush();
        wal_.close();
    }

    static uint64_t now_ns() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count();
    }

private:

    BrokerBridge* broker_;
    FeedbackEmitter* feedback_;

    std::atomic<bool> running_;

    boost::lockfree::queue<TradeIntent> intent_queue_;

    std::thread worker_;
    std::thread reconciler_;

    std::mutex state_mutex_;

    std::unordered_set<uint64_t> seen_intents_;
    std::unordered_map<std::string, ExecutionState> executions_;

    std::ofstream wal_;

    double max_spread_ = 2.5;
    int max_positions_per_symbol_ = 2;
};
