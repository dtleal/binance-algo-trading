.PHONY: install start stop redis dashboard bots status-all build-frontend help monitor monitor-trades monitor-kline monitor-ticker monitor-depth short status close history bot bot-dry bot-sand bot-sand-dry bot-mana bot-mana-dry bot-gala bot-gala-dry bot-doge bot-doge-dry bot-shib bot-shib-dry logs clean fetch-data fetch-btc fetch-eth fetch-eth-5m onboarding onboarding-download backtest-sweep backtest-detail backtest-detail-pullback backtest-eth-5m build-sweep sweep-rust sweep-rust-axs sweep-rust-sand sweep-rust-gala sweep-rust-mana sweep-rust-btc sweep-rust-eth analyze-sweep analyze-best pullback-best pullback-best-dry pullback-best-axs pullback-best-sand pullback-best-gala pullback-best-mana pullback-btc pullback-btc-dry pullback-eth pullback-eth-dry

SYMBOL ?= axsusdt
QTY ?= 1
STOP_LOSS ?= 5.0
LEVERAGE ?= 5
DAYS ?= 7

# Colors
GREEN  := \033[0;32m
YELLOW := \033[0;33m
BLUE   := \033[0;34m
RED    := \033[0;31m
NC     := \033[0m

help: ## Show this help
	@echo "$(GREEN)╔════════════════════════════════════════════╗$(NC)"
	@echo "$(GREEN)║    Binance Trader - Command Reference     ║$(NC)"
	@echo "$(GREEN)╚════════════════════════════════════════════╝$(NC)"
	@echo ""
	@echo "$(YELLOW)🚀 Quick Start:$(NC)"
	@echo "  $(BLUE)make install$(NC)  - Install all dependencies"
	@echo "  $(BLUE)make start$(NC)    - Start dashboard + all optimized bots"
	@echo "  $(BLUE)make stop$(NC)     - Stop all processes"
	@echo ""
	@echo "$(YELLOW)📋 All Available Commands:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-22s$(NC) %s\n", $$1, $$2}'
	@echo ""

install: ## Install all dependencies (backend + frontend)
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo "$(GREEN)  Installing Binance Trader$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(YELLOW)📦 Installing backend dependencies...$(NC)"
	@poetry install
	@echo ""
	@echo "$(YELLOW)📦 Installing frontend dependencies...$(NC)"
	@cd frontend && npm install
	@echo ""
	@echo "$(YELLOW)🔨 Building frontend...$(NC)"
	@cd frontend && npm run build
	@echo ""
	@echo "$(GREEN)✅ Installation complete!$(NC)"
	@echo ""
	@echo "$(BLUE)Next steps:$(NC)"
	@echo "  1. Configure your .env file with API keys"
	@echo "  2. Run: $(YELLOW)make start$(NC)"
	@echo ""

build-frontend: ## Build frontend for production
	@echo "$(YELLOW)🔨 Building frontend...$(NC)"
	@cd frontend && npm run build
	@echo "$(GREEN)✅ Frontend built successfully!$(NC)"

redis: ## Start Redis server
	@echo "$(YELLOW)🔌 Starting Redis...$(NC)"
	@if command -v redis-server > /dev/null; then \
		redis-server --daemonize yes 2>/dev/null || true; \
		echo "$(GREEN)✅ Redis running$(NC)"; \
	else \
		echo "$(RED)❌ Redis not installed!$(NC)"; \
		echo "$(YELLOW)Install with: brew install redis$(NC)"; \
		exit 1; \
	fi

dashboard: redis build-frontend ## Start dashboard server only
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo "$(GREEN)  Starting Dashboard$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo ""
	@export REDIS_URL=redis://localhost:6379 && \
		poetry run python -m trader serve --port 8080 --host 0.0.0.0

bots: redis ## Start all validated bots with optimal configurations (auto-reads from trader/config.py)
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo "$(GREEN)  Starting Trading Bots$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(YELLOW)All bots auto-configure from trader/config.py (interval, TP/SL, params)$(NC)"
	@echo ""
	@export REDIS_URL=redis://localhost:6379 && \
		(nohup poetry run python -m trader bot --symbol axsusdt --leverage 20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader bot --symbol sandusdt --leverage 20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader bot --symbol manausdt --leverage 20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader bot --symbol solusdt --leverage 20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol galausdt --leverage 20 --tp 10.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol avaxusdt --leverage 20 --tp 7.0 --sl 2.0 --min-bars 30 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol dogeusdt --leverage 20 --tp 10.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol 1000shibusdt --leverage 20 --tp 7.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol xrpusdt --leverage 20 --tp 10.0 --sl 2.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.20 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol ethusdt --leverage 5 --tp 10.0 --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.30 > /dev/null 2>&1 &)
	@sleep 3
	@echo "$(GREEN)✅ Bots started! (PEPE skipped - invalid symbol)$(NC)"
	@echo ""
	@echo "$(BLUE)Active Strategies (10 bots):$(NC)"
	@echo "  📊 MomShort (20x leverage):"
	@echo "     • AXSUSDT (1m, +40.10%), SANDUSDT (5m, +27.61%)"
	@echo "     • MANAUSDT (1m, +30.54%), SOLUSDT (1m, +28.13%)"
	@echo ""
	@echo "  📊 VWAPPullback:"
	@echo "     • GALAUSDT (1m, 20x, +34.85%), AVAXUSDT (1m, 20x, +31.12%)"
	@echo "     • DOGEUSDT (5m, 20x, +41.28%), 1000SHIBUSDT (5m, 20x, +37.51%)"
	@echo "     • XRPUSDT (5m, 20x, +29.21%), ETHUSDT (5m, 5x, +31.28%)"
	@echo ""

start: redis ## 🚀 Start EVERYTHING (dashboard + all bots)
	@echo "$(GREEN)╔════════════════════════════════════════════╗$(NC)"
	@echo "$(GREEN)║      Starting Binance Trader System        ║$(NC)"
	@echo "$(GREEN)╚════════════════════════════════════════════╝$(NC)"
	@echo ""
	@$(MAKE) -s build-frontend
	@echo ""
	@echo "$(YELLOW)🤖 Starting trading bots...$(NC)"
	@$(MAKE) -s bots
	@echo "$(YELLOW)📊 Starting dashboard...$(NC)"
	@export REDIS_URL=redis://localhost:6379 && \
		nohup poetry run python -m trader serve --port 8080 --host 0.0.0.0 > /dev/null 2>&1 &
	@sleep 2
	@echo ""
	@echo "$(GREEN)╔════════════════════════════════════════════╗$(NC)"
	@echo "$(GREEN)║          ✅ System Started!                ║$(NC)"
	@echo "$(GREEN)╚════════════════════════════════════════════╝$(NC)"
	@echo ""
	@echo "$(BLUE)📊 Dashboard:$(NC) $(YELLOW)http://localhost:8080$(NC)"
	@echo ""
	@echo "$(BLUE)🤖 Active Bots:$(NC) 10 total (PEPE excluded)"
	@echo "   • 4 MomShort bots (3x 1m + 1x 5m, 20x leverage)"
	@echo "   • 6 VWAPPullback bots (4x 5m + 2x 1m, 5x-20x leverage)"
	@echo ""
	@echo "$(BLUE)📝 Useful commands:$(NC)"
	@echo "   • $(YELLOW)make status-all$(NC)  - Check all processes"
	@echo "   • $(YELLOW)make logs$(NC)        - View bot logs"
	@echo "   • $(YELLOW)make stop$(NC)        - Stop everything"
	@echo ""

stop: ## ⛔ Stop all processes (bots + dashboard + redis)
	@echo "$(YELLOW)⛔ Stopping all processes...$(NC)"
	@pkill -f "python -m trader" 2>/dev/null || true
	@pkill -f "redis-server" 2>/dev/null || true
	@echo "$(GREEN)✅ All processes stopped$(NC)"

status-all: ## 📊 Show status of all running processes
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo "$(GREEN)  System Status$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(BLUE)Redis:$(NC)"
	@pgrep -fl redis-server > /dev/null && echo "  $(GREEN)✅ Running$(NC)" || echo "  $(RED)❌ Not running$(NC)"
	@echo ""
	@echo "$(BLUE)Dashboard:$(NC)"
	@pgrep -fl "trader serve" > /dev/null && echo "  $(GREEN)✅ Running$(NC) - http://localhost:8080" || echo "  $(RED)❌ Not running$(NC)"
	@echo ""
	@echo "$(BLUE)MomShort Bots:$(NC)"
	@BOT_COUNT=$$(pgrep -fl "trader bot" | wc -l | tr -d ' '); \
		if [ $$BOT_COUNT -gt 0 ]; then \
			echo "  $(GREEN)✅ $$BOT_COUNT bots running$(NC)"; \
			pgrep -fl "trader bot" | sed 's/^/     /' | grep -o 'symbol [a-z0-9]*' | sed 's/symbol /• /'; \
		else \
			echo "  $(RED)❌ No bots running$(NC)"; \
		fi
	@echo ""
	@echo "$(BLUE)VWAP Pullback Bots:$(NC)"
	@PULLBACK_COUNT=$$(pgrep -fl "trader pullback" | wc -l | tr -d ' '); \
		if [ $$PULLBACK_COUNT -gt 0 ]; then \
			echo "  $(GREEN)✅ $$PULLBACK_COUNT bots running$(NC)"; \
			pgrep -fl "trader pullback" | sed 's/^/     /' | grep -o 'symbol [a-z0-9]*' | sed 's/symbol /• /'; \
		else \
			echo "  $(RED)❌ No bots running$(NC)"; \
		fi
	@echo ""

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

bot-gala: ## Run VWAPPullback trading bot for GALAUSDT
	poetry run python -m trader pullback --symbol galausdt --leverage $(LEVERAGE)

bot-gala-dry: ## Run GALAUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol galausdt --dry-run --leverage $(LEVERAGE)

bot-doge: ## Run VWAPPullback trading bot for DOGEUSDT
	poetry run python -m trader pullback --symbol dogeusdt --leverage $(LEVERAGE)

bot-doge-dry: ## Run DOGEUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol dogeusdt --dry-run --leverage $(LEVERAGE)

bot-shib: ## Run VWAPPullback trading bot for 1000SHIBUSDT
	poetry run python -m trader pullback --symbol 1000shibusdt --leverage $(LEVERAGE)

bot-shib-dry: ## Run 1000SHIBUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol 1000shibusdt --dry-run --leverage $(LEVERAGE)

logs: ## Tail the latest log file
	@ls -t logs/*.log 2>/dev/null | head -1 | xargs -r tail -f || echo "No log files found"

clean: ## Remove log files
	rm -rf logs/*.log

# Backtest commands
fetch-data: ## Download historical kline data (edit fetch_klines.py first)
	poetry run python fetch_klines.py

fetch-btc: ## Download 1 year of BTCUSDT 1m klines
	@echo "📥 Downloading BTCUSDT data (1 year)..."
	@sed -i '' 's/SYMBOL = ".*"/SYMBOL = "BTCUSDT"/' fetch_klines.py
	@sed -i '' 's/CSV_FILE = ".*"/CSV_FILE = "btcusdt_1m_klines.csv"/' fetch_klines.py
	@poetry run python fetch_klines.py
	@echo "✅ BTCUSDT data saved to btcusdt_1m_klines.csv"

fetch-eth: ## Download 1 year of ETHUSDT 1m klines
	@echo "📥 Downloading ETHUSDT data (1 year)..."
	@sed -i '' 's/SYMBOL = ".*"/SYMBOL = "ETHUSDT"/' fetch_klines.py
	@sed -i '' 's/CSV_FILE = ".*"/CSV_FILE = "ethusdt_1m_klines.csv"/' fetch_klines.py
	@poetry run python fetch_klines.py
	@echo "✅ ETHUSDT data saved to ethusdt_1m_klines.csv"

fetch-eth-5m: ## Download 1 year of ETHUSDT 5-minute klines (official, for ETH VWAPPullback strategy)
	@echo "📥 Downloading ETHUSDT 5-minute candles (1 year)..."
	@poetry run python fetch_eth_5m_official.py
	@echo "✅ ETHUSDT 5m data saved to ethusdt_5m_klines_official.csv"

# ══════════════════════════════════════════════════════════════════════════════
# 📚 ONBOARDING - Automated new asset validation
# ══════════════════════════════════════════════════════════════════════════════

onboarding: ## Run complete onboarding for new asset (ATIVO=DOGEUSDT)
ifndef ATIVO
	@echo "$(RED)❌ Error: ATIVO not specified$(NC)"
	@echo ""
	@echo "$(YELLOW)Usage:$(NC)"
	@echo "  make onboarding ATIVO=DOGEUSDT"
	@echo "  make onboarding ATIVO=1000SHIBUSDT"
	@echo "  make onboarding ATIVO=ETHUSDT STRATEGY=pullback"
	@echo ""
	@echo "$(YELLOW)Options:$(NC)"
	@echo "  ATIVO     - Trading pair symbol (required)"
	@echo "  STRATEGY  - Strategy type: momshort | pullback (default: momshort)"
	@echo "  DAYS      - Days of historical data (default: 365)"
	@echo ""
	@exit 1
endif
	@echo "$(GREEN)╔════════════════════════════════════════════════════════════╗$(NC)"
	@echo "$(GREEN)║           Starting Onboarding: $(ATIVO)                    ║$(NC)"
	@echo "$(GREEN)╚════════════════════════════════════════════════════════════╝$(NC)"
	@echo ""
	@poetry run python onboarding.py $(ATIVO) $(if $(STRATEGY),--strategy $(STRATEGY),) $(if $(DAYS),--days $(DAYS),)

onboarding-download: ## Download historical data only (ATIVO=DOGEUSDT DAYS=365)
ifndef ATIVO
	@echo "$(RED)❌ Error: ATIVO not specified. Usage: make onboarding-download ATIVO=DOGEUSDT$(NC)"
	@exit 1
endif
	@echo "$(YELLOW)📥 Downloading $(or $(DAYS),365) days of $(ATIVO) data...$(NC)"
	@poetry run python fetch_klines.py $(ATIVO) $(if $(DAYS),-d $(DAYS),)

# ══════════════════════════════════════════════════════════════════════════════
# 🧪 BACKTESTING
# ══════════════════════════════════════════════════════════════════════════════

backtest-sweep: ## Run MomShort parameter sweep (edit backtest_sweep.py first)
	poetry run python backtest_sweep.py

backtest-detail: ## Run detailed MomShort backtest (edit backtest_detail.py first)
	poetry run python backtest_detail.py

backtest-detail-pullback: ## Run detailed VWAPPullback backtest (edit backtest_detail_pullback.py first)
	poetry run python backtest_detail_pullback.py

backtest-eth-5m: ## Run ETH 5min VWAPPullback backtest with optimized params (+31.38% return)
	@echo "📊 Running ETH 5min VWAPPullback backtest..."
	@poetry run python backtest_eth_5m_FINAL.py

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

sweep-rust-btc: ## Run Rust sweep for BTCUSDT (all 5 strategies)
	@$(MAKE) sweep-rust SYMBOL=btcusdt

sweep-rust-eth: ## Run Rust sweep for ETHUSDT (all 5 strategies)
	@$(MAKE) sweep-rust SYMBOL=ethusdt

# Analyze sweep results
analyze-sweep: ## Show top 5 VWAPPullback configs from Rust sweep
	poetry run python analyze_sweep.py --top 5

analyze-best: ## Auto-run detailed backtest on BEST VWAPPullback config
	poetry run python analyze_sweep.py --run-best

# VWAPPullback bot with OPTIMIZED parameters from sweep
# Best config for AXS/SAND/GALA/MANA (1min candles): TP=10% SL=5% EMA=200 bars=5 cfm=1 vwap_prox=0.5% vwap_window=10d max_trades=1
PULLBACK_BEST_PARAMS = --tp 10.0 --sl 5.0 --min-bars 5 --confirm-bars 1 --vwap-prox 0.005 --vwap-window-days 10 --ema-period 200 --pos-size 0.20 --max-trades 1

# ETH 5min optimized params: +31.38% return, 281 trades, 49.8% win rate, 6.47% max DD
# ⚠️  IMPORTANT: ETH strategy uses 5-minute candles (not 1min)!
# Position size: 30% (min $20 notional - requires $67+ capital)
PULLBACK_ETH_5M_PARAMS = --tp 10.0 --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --vwap-window-days 1 --ema-period 100 --pos-size 0.30 --max-trades 2

pullback-best: ## Run VWAPPullback bot for AXSUSDT with BEST parameters (LEVERAGE=5)
	poetry run python -m trader pullback --symbol axsusdt --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-best-dry: ## Run VWAPPullback bot for AXSUSDT in DRY-RUN mode with BEST parameters
	poetry run python -m trader pullback --symbol axsusdt --dry-run --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-best-axs: ## Run VWAPPullback bot for AXSUSDT with BEST parameters
	poetry run python -m trader pullback --symbol axsusdt --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-best-sand: ## Run VWAPPullback bot for SANDUSDT with BEST parameters
	poetry run python -m trader pullback --symbol sandusdt --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-best-gala: ## Run VWAPPullback bot for GALAUSDT with BEST parameters
	poetry run python -m trader pullback --symbol galausdt --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-best-mana: ## Run VWAPPullback bot for MANAUSDT with BEST parameters
	poetry run python -m trader pullback --symbol manausdt --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

# BTC/ETH - Use these after running sweep to find best params
pullback-btc: ## Run VWAPPullback bot for BTCUSDT (run sweep-rust-btc first!)
	@echo "⚠️  Make sure to run 'make sweep-rust-btc' first to find best params!"
	poetry run python -m trader pullback --symbol btcusdt --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-btc-dry: ## Run VWAPPullback bot for BTCUSDT in DRY-RUN mode
	@echo "⚠️  Using default params. Run 'make sweep-rust-btc' to optimize."
	poetry run python -m trader pullback --symbol btcusdt --dry-run --leverage $(LEVERAGE) $(PULLBACK_BEST_PARAMS)

pullback-eth: ## Run VWAPPullback bot for ETHUSDT with optimized 5min params (LEVERAGE=5, pos_size=30%)
	@echo "🚀 Starting ETHUSDT VWAPPullback bot (5min candles)"
	@echo "📊 Optimized params: TP=10% SL=5% EMA=100 bars=20 cfm=0 max_trades=2"
	@echo "💰 Expected: +31.38% annual return | Win rate: 49.8% | Max DD: 6.47%"
	@echo "⚙️  Leverage: $(LEVERAGE)x | Position size: 30% per trade (min capital: $67)"
	@echo ""
	poetry run python -m trader pullback --symbol ethusdt --leverage $(LEVERAGE) $(PULLBACK_ETH_5M_PARAMS)

pullback-eth-dry: ## Run VWAPPullback bot for ETHUSDT in DRY-RUN mode (5min optimized params)
	@echo "🧪 DRY-RUN mode: ETHUSDT VWAPPullback (5min candles)"
	@echo "📊 Params: TP=10% SL=5% EMA=100 bars=20 cfm=0 max_trades=2 pos_size=30%"
	@echo "💰 Backtest result: +31.38% return | 49.8% win rate | 6.47% max DD"
	@echo ""
	poetry run python -m trader pullback --symbol ethusdt --dry-run --leverage $(LEVERAGE) $(PULLBACK_ETH_5M_PARAMS)
