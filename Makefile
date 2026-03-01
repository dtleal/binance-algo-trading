.PHONY: install start stop redis dashboard bots status-all build-frontend help monitor monitor-trades monitor-kline monitor-ticker monitor-depth short status close history bot bot-dry bot-sand bot-sand-dry bot-mana bot-mana-dry bot-gala bot-gala-dry bot-doge bot-doge-dry bot-shib bot-shib-dry bot-xau bot-xau-dry bot-zec bot-zec-dry bot-ksm-orb bot-ksm-orb-dry bot-magic-pdhl bot-magic-pdhl-dry logs clean fetch-data fetch-btc fetch-eth fetch-eth-5m onboarding onboarding-download backtest-sweep backtest-detail backtest-detail-pullback backtest-eth-5m build-sweep sweep-rust sweep-rust-axs sweep-rust-sand sweep-rust-gala sweep-rust-mana sweep-rust-btc sweep-rust-eth analyze-sweep analyze-best pullback-best pullback-best-dry pullback-best-axs pullback-best-sand pullback-best-gala pullback-best-mana pullback-btc pullback-btc-dry pullback-eth pullback-eth-dry build-sweep-v2 sweep-v2 bots-v2 bot-gala-v2 bot-gala-v2-dry bot-avax-v2 bot-avax-v2-dry bot-doge-v2 bot-doge-v2-dry bot-shib-v2 bot-shib-v2-dry bot-xrp-v2 bot-xrp-v2-dry bot-eth-v2 bot-eth-v2-dry bot-xau-v2 bot-xau-v2-dry bot-btc-ema bot-btc-ema-dry bot-btc-orb bot-btc-orb-dry bot-btc-pdhl bot-btc-pdhl-dry bot-ltc-pdhl bot-ltc-pdhl-dry bot-link-pdhl bot-link-pdhl-dry bot-bch-pdhl bot-bch-pdhl-dry

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
		(nohup poetry run python -m trader bot --symbol axsusdt --leverage 30 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader bot --symbol sandusdt --leverage 30 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader bot --symbol manausdt --leverage 30 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader bot --symbol solusdt --leverage 30 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol galausdt --leverage 30 --tp 10.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol avaxusdt --leverage 30 --tp 7.0 --sl 2.0 --min-bars 30 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol dogeusdt --leverage 30 --tp 10.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol 1000shibusdt --leverage 30 --tp 7.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol xrpusdt --leverage 30 --tp 10.0 --sl 2.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol ethusdt --leverage 10 --tp 10.0 --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol xauusdt --leverage 30 --tp 5.0 --sl 5.0 --min-bars 3 --confirm-bars 1 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pdhl --symbol ltcusdt --leverage 30 --sl 5.0 --prox-pct 0.001 --confirm-bars 1 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pdhl --symbol linkusdt --leverage 30 --sl 5.0 --prox-pct 0.0 --confirm-bars 2 --pos-size 0.40 --tp 10.0 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pdhl --symbol bchusdt --leverage 30 --sl 5.0 --prox-pct 0.005 --confirm-bars 1 --pos-size 0.40 --tp 10.0 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol xmrusdt --leverage 30 --tp 7.0 --sl 5.0 --min-bars 8 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol uniusdt --leverage 30 --tp 10.0 --sl 2.0 --min-bars 3 --confirm-bars 1 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol aptusdt --leverage 30 --tp 10.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol 1000pepeusdt --leverage 30 --tp 10.0 --sl 5.0 --min-bars 5 --confirm-bars 2 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol dashusdt --leverage 30 --tp 5.0 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback --symbol zecusdt --leverage 30 --tp 10.0 --sl 5.0 --min-bars 8 --confirm-bars 2 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader orb --symbol ksmusdt --leverage 30 --sl 5.0 --range-mins 60 --be-r 2.0 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pdhl --symbol magicusdt --leverage 30 --sl 5.0 --prox-pct 0.0 --confirm-bars 1 --pos-size 0.40 --tp 10.0 > /dev/null 2>&1 &)
	@sleep 3
	@echo "$(GREEN)✅ Bots started!$(NC)"
	@echo ""
	@echo "$(BLUE)Active Strategies (22 bots):$(NC)"
	@echo "  📊 MomShort (30x leverage):"
	@echo "     • AXSUSDT (1m, +40.10%), SANDUSDT (5m, +27.61%)"
	@echo "     • MANAUSDT (1m, +30.54%), SOLUSDT (1m, +28.13%)"
	@echo ""
	@echo "  📊 VWAPPullback:"
	@echo "     • GALAUSDT (1m, 30x, +34.85%), AVAXUSDT (1m, 30x, +31.12%)"
	@echo "     • DOGEUSDT (5m, 30x, +42.75%), 1000SHIBUSDT (5m, 30x, +37.51%)"
	@echo "     • XRPUSDT (5m, 30x, +30.15%), ETHUSDT (5m, 10x, +31.87%)"
	@echo "     • XAUUSDT (1m, 30x, +7.67%), XMRUSDT (1m, 30x, +35.76%)"
	@echo "     • UNIUSDT (15m, 30x, +31.71%), APTUSDT (5m, 30x, +19.66%)"
	@echo "     • 1000PEPEUSDT (5m, 30x, +38.86%), DASHUSDT (15m, 30x, +22.06%)"
	@echo "     • ZECUSDT (5m, 30x, +25.55%)"
	@echo ""
	@echo "  📊 ORB:"
	@echo "     • KSMUSDT (1h, 30x, +31.95%)"
	@echo ""
	@echo "  📊 PDHL:"
	@echo "     • LTCUSDT (1m, 30x, +50.76%), LINKUSDT (1m, 30x, +115.87%)"
	@echo "     • BCHUSDT (5m, 30x, +68.46%), MAGICUSDT (1h, 30x, +90.75%)"
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
	@echo "$(BLUE)🤖 Active Bots:$(NC) 16 total"
	@echo "   • 4 MomShort bots (3x 1m + 1x 5m, 30x leverage)"
	@echo "   • 9 VWAPPullback bots (5x 5m + 4x 1m, 10x-30x leverage)"
	@echo "   • 3 PDHL bots (1x 1m + 2x 5m, 30x leverage)"
	@echo ""
	@echo "$(BLUE)📝 Useful commands:$(NC)"
	@echo "   • $(YELLOW)make status-all$(NC)  - Check all processes"
	@echo "   • $(YELLOW)make logs$(NC)        - View bot logs"
	@echo "   • $(YELLOW)make stop$(NC)        - Stop everything"
	@echo ""

stop: ## ⛔ Stop all processes (bots + dashboard + redis)
	@echo "$(YELLOW)⛔ Stopping all processes...$(NC)"
	@pkill -f "python -m trader" 2>/dev/null || true
	@pkill -9 -f "trader serve" 2>/dev/null || true
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

bot-xau: ## Run VWAPPullback trading bot for XAUUSDT (Gold)
	poetry run python -m trader pullback --symbol xauusdt --leverage $(LEVERAGE)

bot-xau-dry: ## Run XAUUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol xauusdt --dry-run --leverage $(LEVERAGE)

bot-pepe: ## Run VWAPPullback trading bot for 1000PEPEUSDT
	poetry run python -m trader pullback --symbol 1000pepeusdt --leverage $(LEVERAGE)

bot-pepe-dry: ## Run 1000PEPEUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol 1000pepeusdt --dry-run --leverage $(LEVERAGE)

bot-uni: ## Run VWAPPullback trading bot for UNIUSDT
	poetry run python -m trader pullback --symbol uniusdt --leverage $(LEVERAGE)

bot-uni-dry: ## Run UNIUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol uniusdt --dry-run --leverage $(LEVERAGE)

bot-apt: ## Run VWAPPullback trading bot for APTUSDT
	poetry run python -m trader pullback --symbol aptusdt --leverage $(LEVERAGE)

bot-apt-dry: ## Run APTUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol aptusdt --dry-run --leverage $(LEVERAGE)

bot-zec: ## Run VWAPPullback trading bot for ZECUSDT
	poetry run python -m trader pullback --symbol zecusdt --leverage $(LEVERAGE)

bot-zec-dry: ## Run ZECUSDT bot in dry-run mode
	poetry run python -m trader pullback --symbol zecusdt --dry-run --leverage $(LEVERAGE)

bot-ksm-orb: ## Run ORB bot for KSMUSDT (1h, tp~10% via be-r=2, sl=5%, range=60min, champion +31.95%)
	poetry run python -m trader orb --symbol ksmusdt --leverage $(LEVERAGE) --sl 5.0 --range-mins 60 --be-r 2.0 --pos-size 0.40

bot-ksm-orb-dry: ## Run KSMUSDT ORB bot in dry-run mode
	poetry run python -m trader orb --symbol ksmusdt --leverage $(LEVERAGE) --sl 5.0 --range-mins 60 --be-r 2.0 --pos-size 0.40 --dry-run

logs: ## Tail the latest log file
	@ls -t logs/*.log 2>/dev/null | head -1 | xargs -r tail -f || echo "No log files found"

clean: ## Remove log files
	rm -rf logs/*.log

# Backtest commands
fetch-data: ## Download historical kline data (SYMBOL=axsusdt DAYS=365)
	poetry run python scripts/fetch_klines.py $(shell echo $(SYMBOL) | tr '[:lower:]' '[:upper:]') -d $(DAYS) -o data/klines/$(SYMBOL)_1m_klines.csv

fetch-btc: ## Download 1 year of BTCUSDT 1m klines
	@echo "📥 Downloading BTCUSDT data (1 year)..."
	@poetry run python scripts/fetch_klines.py BTCUSDT -d 365 -o data/klines/btcusdt_1m_klines.csv
	@echo "✅ BTCUSDT data saved to data/klines/btcusdt_1m_klines.csv"

fetch-eth: ## Download 1 year of ETHUSDT 1m klines
	@echo "📥 Downloading ETHUSDT data (1 year)..."
	@poetry run python scripts/fetch_klines.py ETHUSDT -d 365 -o data/klines/ethusdt_1m_klines.csv
	@echo "✅ ETHUSDT data saved to data/klines/ethusdt_1m_klines.csv"

fetch-eth-5m: ## Download 1 year of ETHUSDT 5-minute klines (official, for ETH VWAPPullback strategy)
	@echo "📥 Downloading ETHUSDT 5-minute candles (1 year)..."
	@poetry run python scripts/fetch_eth_5m_official.py
	@echo "✅ ETHUSDT 5m data saved to data/klines/ethusdt_5m_klines_official.csv"

# ══════════════════════════════════════════════════════════════════════════════
# 📚 ONBOARDING - Automated new asset validation
# ══════════════════════════════════════════════════════════════════════════════

onboarding: ## Full onboarding: download → aggregate → sweep all timeframes (SYMBOL=dogeusdt)
ifeq ($(filter command line environment,$(origin SYMBOL)),)
	@echo "$(RED)❌ Error: SYMBOL not specified$(NC)"
	@echo ""
	@echo "$(YELLOW)Usage:$(NC)"
	@echo "  make onboarding SYMBOL=dogeusdt"
	@echo "  make onboarding SYMBOL=btcusdt DAYS=365"
	@echo ""
	@echo "$(YELLOW)Steps executed:$(NC)"
	@echo "  1. Download 1m historical data"
	@echo "  2. Aggregate to 5m, 15m, 30m, 1h  (MANDATORY)"
	@echo "  3. Run parameter sweep on all available timeframes"
	@echo ""
	@exit 1
else
	@SYMBOL_UPPER=$$(echo "$(SYMBOL)" | tr '[:lower:]' '[:upper:]'); \
	FETCH_DAYS=$$([ "$(DAYS)" = "7" ] && echo "365" || echo "$(DAYS)"); \
	echo "$(GREEN)════════════════════════════════════════════════════$(NC)"; \
	echo "$(GREEN)  Onboarding: $$SYMBOL_UPPER  ($$FETCH_DAYS days)$(NC)"; \
	echo "$(GREEN)════════════════════════════════════════════════════$(NC)"; \
	echo ""; \
	echo "$(YELLOW)── Step 1: Download 1m historical data ──$(NC)"; \
	mkdir -p data/klines data/sweeps; \
	poetry run python scripts/fetch_klines.py $$SYMBOL_UPPER -d $$FETCH_DAYS -o data/klines/$(SYMBOL)_1m_klines.csv; \
	if [ ! -f "data/klines/$(SYMBOL)_1m_klines.csv" ]; then \
		echo "$(RED)❌ Download failed: data/klines/$(SYMBOL)_1m_klines.csv not found$(NC)"; exit 1; \
	fi; \
	echo ""; \
	echo "$(YELLOW)── Step 2: Aggregate to 5m / 15m / 30m / 1h ──$(NC)"; \
	poetry run python scripts/aggregate_klines.py data/klines/$(SYMBOL)_1m_klines.csv; \
	echo ""; \
	echo "$(YELLOW)── Step 3: Parameter sweep — all timeframes ──$(NC)"; \
	BINARY=./backtest_sweep/target/release/backtest_sweep; \
	if [ ! -f "$$BINARY" ]; then \
		echo "$(RED)❌ Sweep binary not found. Run: make build-sweep$(NC)"; exit 1; \
	fi; \
	for TF in 1m 5m 15m 30m 1h; do \
		CSV="data/klines/$(SYMBOL)_$${TF}_klines.csv"; \
		if [ ! -f "$$CSV" ]; then echo "  ⏭  $$CSV not found, skipping"; continue; fi; \
		echo ""; \
		echo "$(YELLOW)━━━━ Sweep: $$TF  →  $$CSV ━━━━$(NC)"; \
		$$BINARY $$CSV; \
		mv backtest_sweep.csv "data/sweeps/$(SYMBOL)_$${TF}_sweep.csv" 2>/dev/null || true; \
		echo "  📄 Results saved → data/sweeps/$(SYMBOL)_$${TF}_sweep.csv"; \
	done; \
	echo ""; \
	echo "$(GREEN)════════════════════════════════════════════════════$(NC)"; \
	echo "$(GREEN)  ✅ Onboarding complete — $(SYMBOL)$(NC)"; \
	echo "$(GREEN)  Review sweep CSVs: data/sweeps/$(SYMBOL)_*_sweep.csv$(NC)"; \
	echo "$(GREEN)════════════════════════════════════════════════════$(NC)"
endif

onboarding-download: ## Download 1m data only (SYMBOL=dogeusdt DAYS=365)
ifeq ($(filter command line environment,$(origin SYMBOL)),)
	@echo "$(RED)❌ Error: SYMBOL not specified. Usage: make onboarding-download SYMBOL=dogeusdt$(NC)"
	@exit 1
else
	@SYMBOL_UPPER=$$(echo "$(SYMBOL)" | tr '[:lower:]' '[:upper:]'); \
	mkdir -p data/klines; \
	echo "$(YELLOW)📥 Downloading $(or $(DAYS),365) days of $$SYMBOL_UPPER data...$(NC)"; \
	poetry run python scripts/fetch_klines.py $$SYMBOL_UPPER $(if $(DAYS),-d $(DAYS),) -o data/klines/$(SYMBOL)_1m_klines.csv
endif

# ══════════════════════════════════════════════════════════════════════════════
# 🧪 BACKTESTING
# ══════════════════════════════════════════════════════════════════════════════

backtest-sweep: ## Run MomShort parameter sweep (edit scripts/backtest_sweep.py first)
	poetry run python scripts/backtest_sweep.py

backtest-detail: ## Run detailed MomShort backtest (edit scripts/backtest_detail.py first)
	poetry run python scripts/backtest_detail.py

backtest-detail-pullback: ## Run detailed VWAPPullback backtest (edit scripts/backtest_detail_pullback.py first)
	poetry run python scripts/backtest_detail_pullback.py

backtest-eth-5m: ## Run ETH 5min VWAPPullback backtest with optimized params (+31.38% return)
	@echo "📊 Running ETH 5min VWAPPullback backtest..."
	@poetry run python scripts/backtest_eth_5m_FINAL.py

# Rust sweep (240x faster!)
build-sweep: ## Build Rust sweep (release mode)
	cd backtest_sweep && cargo build --release

sweep-rust: ## Run standard sweep across all available timeframes (SYMBOL=axsusdt)
ifeq ($(filter command line environment,$(origin SYMBOL)),)
	@echo "$(RED)❌ Error: SYMBOL not specified. Usage: make sweep-rust SYMBOL=dogeusdt$(NC)"
	@exit 1
else
	@mkdir -p data/sweeps; \
	BINARY=./backtest_sweep/target/release/backtest_sweep; \
	if [ ! -f "$$BINARY" ]; then echo "$(RED)❌ Binary not found. Run: make build-sweep$(NC)"; exit 1; fi; \
	FOUND=0; \
	for TF in 1m 5m 15m 30m 1h; do \
		CSV="data/klines/$(SYMBOL)_$${TF}_klines.csv"; \
		if [ ! -f "$$CSV" ]; then echo "  ⏭  $$CSV not found, skipping"; continue; fi; \
		FOUND=1; \
		echo ""; \
		echo "$(YELLOW)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"; \
		echo "$(YELLOW)  Timeframe: $$TF  →  $$CSV$(NC)"; \
		echo "$(YELLOW)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"; \
		$$BINARY $$CSV; \
		mv backtest_sweep.csv "data/sweeps/$(SYMBOL)_$${TF}_sweep.csv" 2>/dev/null || true; \
		echo "  📄 Results saved → data/sweeps/$(SYMBOL)_$${TF}_sweep.csv"; \
	done; \
	if [ "$$FOUND" -eq 0 ]; then echo "$(RED)❌ No kline CSVs found for $(SYMBOL) in data/klines/$(NC)"; exit 1; fi
endif

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
	poetry run python scripts/analyze_sweep.py --top 5

analyze-best: ## Auto-run detailed backtest on BEST VWAPPullback config
	poetry run python scripts/analyze_sweep.py --run-best

# VWAPPullback bot with OPTIMIZED parameters from sweep
# Best config for AXS/SAND/GALA/MANA (1min candles): TP=10% SL=5% EMA=200 bars=5 cfm=1 vwap_prox=0.5% vwap_window=10d max_trades=1
PULLBACK_BEST_PARAMS = --tp 10.0 --sl 5.0 --min-bars 5 --confirm-bars 1 --vwap-prox 0.005 --vwap-window-days 10 --ema-period 200 --pos-size 0.40 --max-trades 1

# ETH 5min optimized params: +31.38% return, 281 trades, 49.8% win rate, 6.47% max DD
# ⚠️  IMPORTANT: ETH strategy uses 5-minute candles (not 1min)!
# Position size: 30% (min $20 notional - requires $67+ capital)
PULLBACK_ETH_5M_PARAMS = --tp 10.0 --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --vwap-window-days 1 --ema-period 100 --pos-size 0.40 --max-trades 2

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

# ══════════════════════════════════════════════════════════════════════════════
# 🔄 V2 — R-multiple trailing stop (no fixed TP)
# ══════════════════════════════════════════════════════════════════════════════

build-sweep-v2: ## Build Rust V2 sweep binary (trailing stop, no TP)
	cd backtest_sweep_v2 && cargo build --release

sweep-v2: ## Run V2 sweep across all available timeframes for SYMBOL (e.g. make sweep-v2 SYMBOL=ethusdt)
ifeq ($(filter command line environment,$(origin SYMBOL)),)
	@echo "$(RED)❌ Error: SYMBOL not specified$(NC)"
	@echo "$(YELLOW)Usage: make sweep-v2 SYMBOL=ethusdt$(NC)"
	@exit 1
else
	@echo "$(GREEN)═══════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)  V2 Sweep — $(SYMBOL) — all timeframes$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════$(NC)"
	@mkdir -p data/sweeps; \
	BINARY=./backtest_sweep_v2/target/release/backtest_sweep_v2; \
	if [ ! -f "$$BINARY" ]; then echo "$(RED)❌ Binary not found. Run: make build-sweep-v2$(NC)"; exit 1; fi; \
	FOUND=0; \
	for TF in 1m 5m 15m 30m 1h; do \
		CSV_FILE="data/klines/$(SYMBOL)_$${TF}_klines.csv"; \
		ALT_FILE="data/klines/$(SYMBOL)_$${TF}_klines_official.csv"; \
		if [ -f "$$ALT_FILE" ]; then CSV_FILE="$$ALT_FILE"; fi; \
		if [ ! -f "$$CSV_FILE" ]; then echo "  ⏭  $$CSV_FILE not found, skipping"; continue; fi; \
		FOUND=1; \
		echo ""; \
		echo "$(YELLOW)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"; \
		echo "$(YELLOW)  Timeframe: $$TF  →  $$CSV_FILE$(NC)"; \
		echo "$(YELLOW)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"; \
		$$BINARY $$CSV_FILE; \
		if [ -f "backtest_sweep_v2.csv" ]; then \
			mv backtest_sweep_v2.csv "data/sweeps/$(SYMBOL)_$${TF}_sweep_v2.csv"; \
			echo "  📄 Results saved → data/sweeps/$(SYMBOL)_$${TF}_sweep_v2.csv"; \
		fi; \
	done; \
	if [ "$$FOUND" -eq 0 ]; then echo "$(RED)❌ No kline CSVs found for $(SYMBOL) in data/klines/$(NC)"; exit 1; fi
endif

# V2 bots — VWAPPullback with R-multiple trailing stop (same SL params as V1, no TP)
bot-gala-v2: ## Run VWAPPullback V2 bot for GALAUSDT
	poetry run python -m trader pullback-v2 --symbol galausdt --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40

bot-gala-v2-dry: ## Run GALAUSDT V2 bot in dry-run mode
	poetry run python -m trader pullback-v2 --symbol galausdt --dry-run --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40

bot-avax-v2: ## Run VWAPPullback V2 bot for AVAXUSDT
	poetry run python -m trader pullback-v2 --symbol avaxusdt --leverage $(LEVERAGE) --sl 2.0 --min-bars 30 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40

bot-avax-v2-dry: ## Run AVAXUSDT V2 bot in dry-run mode
	poetry run python -m trader pullback-v2 --symbol avaxusdt --dry-run --leverage $(LEVERAGE) --sl 2.0 --min-bars 30 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40

bot-doge-v2: ## Run VWAPPullback V2 bot for DOGEUSDT
	poetry run python -m trader pullback-v2 --symbol dogeusdt --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40

bot-doge-v2-dry: ## Run DOGEUSDT V2 bot in dry-run mode
	poetry run python -m trader pullback-v2 --symbol dogeusdt --dry-run --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40

bot-shib-v2: ## Run VWAPPullback V2 bot for 1000SHIBUSDT
	poetry run python -m trader pullback-v2 --symbol 1000shibusdt --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40

bot-shib-v2-dry: ## Run 1000SHIBUSDT V2 bot in dry-run mode
	poetry run python -m trader pullback-v2 --symbol 1000shibusdt --dry-run --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40

bot-xrp-v2: ## Run VWAPPullback V2 bot for XRPUSDT
	poetry run python -m trader pullback-v2 --symbol xrpusdt --leverage $(LEVERAGE) --sl 2.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40

bot-xrp-v2-dry: ## Run XRPUSDT V2 bot in dry-run mode
	poetry run python -m trader pullback-v2 --symbol xrpusdt --dry-run --leverage $(LEVERAGE) --sl 2.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40

bot-eth-v2: ## Run VWAPPullback V2 bot for ETHUSDT (5min candles)
	poetry run python -m trader pullback-v2 --symbol ethusdt --leverage $(LEVERAGE) --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 --max-trades 2

bot-eth-v2-dry: ## Run ETHUSDT V2 bot in dry-run mode (5min candles)
	poetry run python -m trader pullback-v2 --symbol ethusdt --dry-run --leverage $(LEVERAGE) --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 --max-trades 2

bot-xau-v2: ## Run VWAPPullback V2 bot for XAUUSDT (Gold)
	poetry run python -m trader pullback-v2 --symbol xauusdt --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 1 --vwap-prox 0.005 --pos-size 0.40

bot-xau-v2-dry: ## Run XAUUSDT V2 bot in dry-run mode
	poetry run python -m trader pullback-v2 --symbol xauusdt --dry-run --leverage $(LEVERAGE) --sl 5.0 --min-bars 3 --confirm-bars 1 --vwap-prox 0.005 --pos-size 0.40

# ── Aggressive strategies (EMAScalp / ORB / PDHL) ──────────────────────────

bot-btc-ema: ## Run EMAScalp bot for BTCUSDT (fast=8 slow=21 sl=0.3% leverage=20)
	poetry run python -m trader ema-scalp --symbol btcusdt --leverage 30 --sl 0.3 --fast-period 8 --slow-period 21 --pos-size 0.40

bot-btc-ema-dry: ## Run EMAScalp bot for BTCUSDT in dry-run mode
	poetry run python -m trader ema-scalp --symbol btcusdt --leverage 30 --sl 0.3 --fast-period 8 --slow-period 21 --pos-size 0.40 --dry-run

bot-btc-orb: ## Run ORB bot for BTCUSDT (range=30min sl=0.5% leverage=20)
	poetry run python -m trader orb --symbol btcusdt --leverage 30 --sl 0.5 --range-mins 30 --pos-size 0.40

bot-btc-orb-dry: ## Run ORB bot for BTCUSDT in dry-run mode
	poetry run python -m trader orb --symbol btcusdt --leverage 30 --sl 0.5 --range-mins 30 --pos-size 0.40 --dry-run

bot-btc-pdhl: ## Run PDHL bot for BTCUSDT (prox=0.2% sl=0.3% leverage=20)
	poetry run python -m trader pdhl --symbol btcusdt --leverage 30 --sl 0.3 --prox-pct 0.002 --pos-size 0.40

bot-btc-pdhl-dry: ## Run PDHL bot for BTCUSDT in dry-run mode
	poetry run python -m trader pdhl --symbol btcusdt --leverage 30 --sl 0.3 --prox-pct 0.002 --pos-size 0.40 --dry-run

bot-ltc-pdhl: ## Run PDHL bot for LTCUSDT (prox=0.1% sl=5.0% leverage=30, champion +50.76%)
	poetry run python -m trader pdhl --symbol ltcusdt --leverage 30 --sl 5.0 --prox-pct 0.001 --confirm-bars 1 --pos-size 0.40

bot-ltc-pdhl-dry: ## Run PDHL bot for LTCUSDT in dry-run mode
	poetry run python -m trader pdhl --symbol ltcusdt --leverage 30 --sl 5.0 --prox-pct 0.001 --confirm-bars 1 --pos-size 0.40 --dry-run

bot-link-pdhl: ## Run PDHL bot for LINKUSDT (tp=10% sl=5% cf=2 leverage=30, champion +115.87%)
	poetry run python -m trader pdhl --symbol linkusdt --leverage 30 --sl 5.0 --prox-pct 0.0 --confirm-bars 2 --pos-size 0.40 --tp 10.0

bot-link-pdhl-dry: ## Run PDHL bot for LINKUSDT in dry-run mode
	poetry run python -m trader pdhl --symbol linkusdt --leverage 30 --sl 5.0 --prox-pct 0.0 --confirm-bars 2 --pos-size 0.40 --tp 10.0 --dry-run

bot-bch-pdhl: ## Run PDHL bot for BCHUSDT (tp=10% sl=5% cf=1 prox=0.5% leverage=30, champion +68.46%)
	poetry run python -m trader pdhl --symbol bchusdt --leverage 30 --sl 5.0 --prox-pct 0.005 --confirm-bars 1 --pos-size 0.40 --tp 10.0

bot-bch-pdhl-dry: ## Run PDHL bot for BCHUSDT in dry-run mode
	poetry run python -m trader pdhl --symbol bchusdt --leverage 30 --sl 5.0 --prox-pct 0.005 --confirm-bars 1 --pos-size 0.40 --tp 10.0 --dry-run

bot-magic-pdhl: ## Run PDHL bot for MAGICUSDT (tp=10% sl=5% cf=1 1h leverage=30, champion +90.75%)
	poetry run python -m trader pdhl --symbol magicusdt --leverage 30 --sl 5.0 --prox-pct 0.0 --confirm-bars 1 --pos-size 0.40 --tp 10.0

bot-magic-pdhl-dry: ## Run PDHL bot for MAGICUSDT in dry-run mode
	poetry run python -m trader pdhl --symbol magicusdt --leverage 30 --sl 5.0 --prox-pct 0.0 --confirm-bars 1 --pos-size 0.40 --tp 10.0 --dry-run

bots-v2: redis ## Start all VWAPPullback V2 bots (trailing stop)
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo "$(GREEN)  Starting V2 Trading Bots (trailing stop)$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════$(NC)"
	@echo ""
	@export REDIS_URL=redis://localhost:6379 && \
		(nohup poetry run python -m trader pullback-v2 --symbol galausdt --leverage 30 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback-v2 --symbol avaxusdt --leverage 30 --sl 2.0 --min-bars 30 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback-v2 --symbol dogeusdt --leverage 30 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.002 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback-v2 --symbol 1000shibusdt --leverage 30 --sl 5.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback-v2 --symbol xrpusdt --leverage 30 --sl 2.0 --min-bars 3 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback-v2 --symbol ethusdt --leverage 10 --sl 5.0 --min-bars 20 --confirm-bars 0 --vwap-prox 0.005 --pos-size 0.40 --max-trades 2 > /dev/null 2>&1 &) && \
		(nohup poetry run python -m trader pullback-v2 --symbol xauusdt --leverage 30 --sl 5.0 --min-bars 3 --confirm-bars 1 --vwap-prox 0.005 --pos-size 0.40 > /dev/null 2>&1 &)
	@sleep 3
	@echo "$(GREEN)✅ V2 bots started!$(NC)"
	@echo ""
	@echo "$(BLUE)Active V2 Strategies (7 bots — R-multiple trailing stop):$(NC)"
	@echo "  📊 VWAPPullback V2:"
	@echo "     • GALAUSDT (1m, 20x), AVAXUSDT (1m, 20x)"
	@echo "     • DOGEUSDT (5m, 20x), 1000SHIBUSDT (5m, 20x)"
	@echo "     • XRPUSDT (5m, 20x), ETHUSDT (5m, 5x)"
	@echo "     • XAUUSDT (1m, 20x)"
	@echo ""
