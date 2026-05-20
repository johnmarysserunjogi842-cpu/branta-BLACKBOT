import threading
import time

class SignalWorker(threading.Thread):

    def __init__(self, pipeline, executor, signal_queue, ui_queue):
        super().__init__()
        self.pipeline = pipeline
        self.executor = executor
        self.signal_queue = signal_queue
        self.ui_queue = ui_queue
        self.running = True

    def run(self):

        while self.running:

            if self.signal_queue.empty():
                time.sleep(0.01)
                continue

            payload = self.signal_queue.get()

            signal = payload["signal"]
            session = payload["session"]
            news = payload.get("news", {})
            market = payload["market"]

            decision = self.pipeline.evaluate(
                signal=signal,
                session_ctx=session,
                news_ctx_by_symbol=news,
                market_state=market
            )

            # send to MT5
            self.executor.execute_decision(decision, market)

            # update UI
            self.ui_queue.put({
                "type": "DECISION",
                "data": decision
            })