.PHONY: install monitor monitor-trades monitor-kline monitor-ticker monitor-depth short status close history bot bot-dry bot-sand bot-sand-dry bot-mana bot-mana-dry bot-gala bot-gala-dry logs clean help fetch-data backtest-sweep backtest-detail backtest-detail-pullback build-sweep sweep-rust sweep-rust-axs sweep-rust-sand sweep-rust-gala sweep-rust-mana analyze-sweep analyze-best

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

backtest-detail-pullback: ## Run detailed VWAPPullback backtest (edit backtest_detail_pullback.py first)
	poetry run python backtest_detail_pullback.py

# Rust sweep (240x faster!)
build-sweep: ## Build Rust sweep (release mode)
	cd backtest_sweep && cargo build --release

sweep-rust: ## Run Rust sweep for all strategies (SYMBOL=axsusdt)
	@if [ ! -f "$(SYMBOL)_1m_klines.csv" ]; then \
		echo "Error: $(SYMBOL)_1m_klines.csv not found. Run 'make fetch-data SYMBOL=$(SYMBOL)' first"; \
		exit 1; \
	fi
	./backtest_sweep/target/release/backtest_sweep $(SYMBOL)_1m_klines.csv

sweep-rust-axs: ## Run Rust sweep for AXSUSDT (all 5 strategies)
	@$(MAKE) sweep-rust SYMBOL=axsusdt

sweep-rust-sand: ## Run Rust sweep for SANDUSDT (all 5 strategies)
	@$(MAKE) sweep-rust SYMBOL=sandusdt

sweep-rust-gala: ## Run Rust sweep for GALAUSDT (all 5 strategies)
	@$(MAKE) sweep-rust SYMBOL=galausdt

sweep-rust-mana: ## Run Rust sweep for MANAUSDT (all 5 strategies)
	@$(MAKE) sweep-rust SYMBOL=manausdt

# Analyze sweep results
analyze-sweep: ## Show top 5 VWAPPullback configs from Rust sweep
	poetry run python analyze_sweep.py --top 5

analyze-best: ## Auto-run detailed backtest on BEST VWAPPullback config
	poetry run python analyze_sweep.py --run-best
