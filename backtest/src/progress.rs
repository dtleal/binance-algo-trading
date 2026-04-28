//! Monitoramento de progresso de loops paralelos sem poluir stdout.
//!
//! - Hot loop incrementa AtomicUsize (zero alocação, ~1-3ns).
//! - Thread separada lê o counter e printa em stderr com `\r` a cada 500ms.
//! - Em não-TTY (pipe pra arquivo, CI), thread não printa nada — output limpo.
//! - Drop pára a thread e imprime newline final.

use std::io::{IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

pub struct Progress {
    /// Compartilhe esse `Arc<AtomicUsize>` em closures de par_iter:
    /// `counter.fetch_add(1, Ordering::Relaxed);`
    pub counter: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    handle: Option<thread::JoinHandle<()>>,
}

impl Progress {
    pub fn start(label: &'static str, total: usize) -> Self {
        let counter = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));

        let counter_t = counter.clone();
        let stop_t = stop.clone();

        let handle = thread::spawn(move || {
            let is_tty = std::io::stderr().is_terminal();
            if !is_tty {
                // Em arquivo/CI: silent. Loop só pra responder ao stop signal.
                while !stop_t.load(Ordering::Relaxed) {
                    thread::sleep(Duration::from_millis(500));
                }
                return;
            }
            let start = Instant::now();
            let mut last_done = 0_usize;
            while !stop_t.load(Ordering::Relaxed) {
                thread::sleep(Duration::from_millis(500));
                let done = counter_t.load(Ordering::Relaxed);
                if done == last_done && done > 0 { continue; }
                last_done = done;
                let elapsed = start.elapsed().as_secs_f64();
                let pct = if total > 0 { 100.0 * done as f64 / total as f64 } else { 0.0 };
                let rate = if elapsed > 0.0 { done as f64 / elapsed } else { 0.0 };
                let eta = if rate > 0.0 && done < total {
                    (total - done) as f64 / rate
                } else { 0.0 };
                let _ = write!(
                    std::io::stderr(),
                    "\r[{label}] {done} / {total} ({pct:.1}%) elapsed={elapsed:.0}s eta={eta:.0}s   "
                );
                let _ = std::io::stderr().flush();
            }
            // Linha final + newline
            let done = counter_t.load(Ordering::Relaxed);
            let elapsed = start.elapsed().as_secs_f64();
            let _ = writeln!(
                std::io::stderr(),
                "\r[{label}] {done} / {total} done in {elapsed:.1}s          "
            );
        });

        Self { counter, stop, handle: Some(handle) }
    }
}

impl Drop for Progress {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() { let _ = h.join(); }
    }
}
