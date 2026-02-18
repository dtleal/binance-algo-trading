.PHONY: install monitor monitor-trades monitor-kline monitor-ticker monitor-depth short status close history bot bot-dry bot-sand bot-sand-dry bot-mana bot-mana-dry bot-gala bot-gala-dry logs clean help fetch-data backtest-sweep backtest-detail backtest-sweep-pullback backtest-detail-pullback

SYMBOL ?= axsusdt
QTY ?= 1
STOP_LOSS ?= 5.0
LEVERAGE ?= 5
DAYS ?= 7

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	poetry install

monitor: ## Start monitor with all streams (SYMBOL=axsusdt)
	poetry run python -m trader monitor --symbol $(SYMBOL)

monitor-trades: ## Monitor only trade stream
	poetry run python -m trader monitor --symbol $(SYMBOL) --streams trade

monitor-kline: ## Monitor only kline stream
	poetry run python -m trader monitor --symbol $(SYMBOL) --streams kline

monitor-ticker: ## Monitor only ticker stream
	poetry run python -m trader monitor --symbol $(SYMBOL) --streams ticker

monitor-depth: ## Monitor only depth stream
	poetry run python -m trader monitor --symbol $(SYMBOL) --streams depth

short: ## Open futures short (QTY=1 STOP_LOSS=5.0 LEVERAGE=5)
	poetry run python -m trader short --quantity $(QTY) --stop-loss $(STOP_LOSS) --leverage $(LEVERAGE)

status: ## Show current futures position status
	poetry run python -m trader status

close: ## Close futures short position
	poetry run python -m trader close

history: ## Show trade history with P&L (DAYS=7)
	poetry run python -m trader history --days $(DAYS)

bot: ## Run MomShort trading bot for AXSUSDT (LEVERAGE=5)
	poetry run python -m trader bot --leverage $(LEVERAGE)

bot-dry: ## Run AXSUSDT bot in dry-run mode
	poetry run python -m trader bot --dry-run --leverage $(LEVERAGE)

bot-sand: ## Run MomShort trading bot for SANDUSDT
	poetry run python -m trader bot --symbol sandusdt --leverage $(LEVERAGE)

bot-sand-dry: ## Run SANDUSDT bot in dry-run mode
	poetry run python -m trader bot --symbol sandusdt --dry-run --leverage $(LEVERAGE)

bot-mana: ## Run MomShort trading bot for MANAUSDT
	poetry run python -m trader bot --symbol manausdt --leverage $(LEVERAGE)

bot-mana-dry: ## Run MANAUSDT bot in dry-run mode
	poetry run python -m trader bot --symbol manausdt --dry-run --leverage $(LEVERAGE)

bot-gala: ## Run MomShort trading bot for GALAUSDT
	poetry run python -m trader bot --symbol galausdt --leverage $(LEVERAGE)

bot-gala-dry: ## Run GALAUSDT bot in dry-run mode
	poetry run python -m trader bot --symbol galausdt --dry-run --leverage $(LEVERAGE)

logs: ## Tail the latest log file
	@ls -t logs/*.log 2>/dev/null | head -1 | xargs -r tail -f || echo "No log files found"

clean: ## Remove log files
	rm -rf logs/*.log

# Backtest commands
fetch-data: ## Download historical kline data (edit fetch_klines.py first)
	poetry run python fetch_klines.py

backtest-sweep: ## Run MomShort parameter sweep (edit backtest_sweep.py first)
	poetry run python backtest_sweep.py

backtest-detail: ## Run detailed MomShort backtest (edit backtest_detail.py first)
	poetry run python backtest_detail.py

backtest-sweep-pullback: ## Run VWAPPullback parameter sweep (edit backtest_sweep_pullback.py first)
	poetry run python backtest_sweep_pullback.py

backtest-detail-pullback: ## Run detailed VWAPPullback backtest (edit backtest_detail_pullback.py first)
	poetry run python backtest_detail_pullback.py
