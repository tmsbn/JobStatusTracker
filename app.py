#!/usr/bin/env python3
"""Job Application Tracker - Desktop UI

A simple Tkinter GUI that runs the job tracker pipeline and streams
the output in real time.
"""

import subprocess
import threading
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RUN_SCRIPT = SCRIPT_DIR / "run.sh"


class JobTrackerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Job Application Tracker")
        self.root.geometry("700x500")
        self.root.minsize(500, 350)
        self.process = None

        # -- Header --
        header = tk.Frame(root, bg="#2c3e50", padx=16, pady=12)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="Job Application Tracker",
            font=("Helvetica", 18, "bold"),
            fg="white",
            bg="#2c3e50",
        ).pack(side=tk.LEFT)

        # -- Button bar --
        bar = tk.Frame(root, padx=16, pady=10)
        bar.pack(fill=tk.X)

        self.run_btn = tk.Button(
            bar,
            text="Run Job Tracker",
            font=("Helvetica", 13, "bold"),
            bg="#27ae60",
            fg="white",
            activebackground="#219a52",
            activeforeground="white",
            padx=20,
            pady=6,
            command=self.run_tracker,
        )
        self.run_btn.pack(side=tk.LEFT)

        self.status_label = tk.Label(
            bar, text="Ready", font=("Helvetica", 12), fg="#7f8c8d"
        )
        self.status_label.pack(side=tk.LEFT, padx=14)

        # -- Output console --
        console_frame = tk.Frame(root, padx=16, pady=(0, 16))
        console_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            console_frame, text="Output", font=("Helvetica", 11, "bold"), anchor=tk.W
        ).pack(fill=tk.X, pady=(0, 4))

        self.console = scrolledtext.ScrolledText(
            console_frame,
            wrap=tk.WORD,
            font=("Menlo", 12),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            state=tk.DISABLED,
            relief=tk.FLAT,
            borderwidth=8,
        )
        self.console.pack(fill=tk.BOTH, expand=True)

        # Tag for highlighting step lines
        self.console.tag_configure("step", foreground="#3498db", font=("Menlo", 12, "bold"))
        self.console.tag_configure("success", foreground="#2ecc71")
        self.console.tag_configure("error", foreground="#e74c3c")

    def append_output(self, text, tag=None):
        self.console.configure(state=tk.NORMAL)
        if tag:
            self.console.insert(tk.END, text, tag)
        else:
            self.console.insert(tk.END, text)
        self.console.see(tk.END)
        self.console.configure(state=tk.DISABLED)

    def classify_line(self, line):
        if line.startswith("Step ") or line.startswith("==="):
            return "step"
        if "Error" in line or "error" in line.lower():
            return "error"
        if "Done!" in line or "ready" in line.lower():
            return "success"
        return None

    def run_tracker(self):
        if self.process and self.process.poll() is None:
            return  # already running

        # Clear console
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.configure(state=tk.DISABLED)

        self.run_btn.configure(state=tk.DISABLED, bg="#95a5a6", text="Running...")
        self.status_label.configure(text="Running...", fg="#f39c12")

        thread = threading.Thread(target=self._execute, daemon=True)
        thread.start()

    def _execute(self):
        try:
            self.process = subprocess.Popen(
                ["bash", str(RUN_SCRIPT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(SCRIPT_DIR),
            )

            for line in self.process.stdout:
                tag = self.classify_line(line)
                self.root.after(0, self.append_output, line, tag)

            self.process.wait()
            rc = self.process.returncode

            if rc == 0:
                self.root.after(0, self._set_done)
            else:
                self.root.after(
                    0, self._set_error, f"Script exited with code {rc}"
                )
        except Exception as e:
            self.root.after(0, self._set_error, str(e))

    def _set_done(self):
        self.status_label.configure(text="Completed", fg="#27ae60")
        self.run_btn.configure(
            state=tk.NORMAL, bg="#27ae60", text="Run Job Tracker"
        )

    def _set_error(self, msg):
        self.status_label.configure(text="Failed", fg="#e74c3c")
        self.append_output(f"\n{msg}\n", "error")
        self.run_btn.configure(
            state=tk.NORMAL, bg="#27ae60", text="Run Job Tracker"
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = JobTrackerApp(root)
    root.mainloop()
