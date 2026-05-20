#include <pybind11/pybind11.h>

namespace py = pybind11;

class TradeExecutor {
public:
    TradeExecutor() {}

    std::string execute_trade(std::string symbol) {
        return "Executed trade for " + symbol;
    }
};

PYBIND11_MODULE(trade_executor, m) {
    py::class_<TradeExecutor>(m, "TradeExecutor")
        .def(py::init<>())
        .def("execute_trade", &TradeExecutor::execute_trade);
}