#!/usr/bin/env python3
"""
gui.py — Interface graphique unifiée (Bourse Direct + Trade Republic)

Le bouton principal "Lancer l'export" demande une validation individuelle
pour chaque courtier avant de lancer l'extraction.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import threading
import queue
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from bourse_direct_scraper import BourseDirectScraper, export_csv as bd_export_csv
from trade_republic_scraper import TradeRepublicScraper

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Palette
C_BG        = "#0f0f13"
C_CARD      = "#16161d"
C_CARD2     = "#1c1c25"
C_BORDER    = "#2a2a38"
C_ACCENT    = "#4f6ef7"
C_ACCENT_H  = "#6b85ff"
C_SUCCESS   = "#3ecf8e"
C_WARNING   = "#f0c060"
C_ERROR     = "#f05a5a"
C_TEXT      = "#e8e8f0"
C_MUTED     = "#6b6b80"
C_LOG_BG    = "#0a0a10"


class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put((record.levelname, self.format(record)))


class CodeDialog(ctk.CTkToplevel):
    """Dialog minimaliste pour la saisie d'un code (2FA SMS / OTP)."""

    def __init__(self, parent, title: str, subtitle: str):
        super().__init__(parent)
        self.title("")
        self.geometry("380x210")
        self.resizable(False, False)
        self.grab_set()
        self.lift()
        self.focus_force()
        self.configure(fg_color=C_CARD)

        self._result: str = ""

        ctk.CTkLabel(
            self, text=title,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=C_TEXT,
        ).pack(padx=28, pady=(28, 4), anchor="w")

        ctk.CTkLabel(
            self, text=subtitle,
            font=ctk.CTkFont(size=12),
            text_color=C_MUTED,
        ).pack(padx=28, anchor="w")

        self._entry = ctk.CTkEntry(
            self,
            placeholder_text="Ex : 123456",
            font=ctk.CTkFont(size=16),
            height=44,
            justify="center",
            fg_color=C_BG,
            border_color=C_BORDER,
            text_color=C_TEXT,
        )
        self._entry.pack(padx=28, pady=(16, 0), fill="x")
        self._entry.bind("<Return>", lambda _: self._submit())
        self._entry.focus()

        ctk.CTkButton(
            self, text="Confirmer",
            height=40,
            fg_color=C_ACCENT,
            hover_color=C_ACCENT_H,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._submit,
        ).pack(padx=28, pady=16, fill="x")

    def _submit(self):
        self._result = self._entry.get().strip()
        self.grab_release()
        self.destroy()

    def get_result(self) -> str:
        self.wait_window()
        return self._result


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=C_BG)

        self.title("Finance — Export multi-courtiers")
        self.geometry("720x900")
        self.minsize(640, 760)

        self.log_queue: queue.Queue = queue.Queue()
        self.code_request_queue: queue.Queue = queue.Queue()
        self.code_response_queue: queue.Queue = queue.Queue()

        self._running = False
        self._worker_thread: threading.Thread | None = None

        self._build_ui()
        self._setup_logging()
        self._poll()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # ── En-tête ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 0))

        ctk.CTkLabel(
            header, text="Finance",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=C_TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Export de portefeuille — Bourse Direct & Trade Republic",
            font=ctk.CTkFont(size=13),
            text_color=C_MUTED,
        ).pack(anchor="w")

        # ── Cards ──
        self._card_bourse_direct()
        self._card_trade_republic()
        self._card_options()

        # ── Bouton principal ──
        self.run_btn = ctk.CTkButton(
            self,
            text="▶   Lancer l'export",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=48,
            corner_radius=12,
            fg_color=C_ACCENT,
            hover_color=C_ACCENT_H,
            text_color=C_TEXT,
            command=self._toggle_run,
        )
        self.run_btn.grid(row=4, column=0, sticky="ew", padx=28, pady=16)

        # ── Journal ──
        self._card_journal()

        # ── Statut ──
        self.status_var = tk.StringVar(value="Prêt.")
        ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=11),
            text_color=C_MUTED,
            anchor="w",
        ).grid(row=6, column=0, sticky="ew", padx=28, pady=(0, 12))

    def _card_bourse_direct(self):
        card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=14, border_width=1, border_color=C_BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=28, pady=(20, 0))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="BOURSE DIRECT",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C_MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(16, 8))

        ctk.CTkLabel(card, text="Login", font=ctk.CTkFont(size=13), text_color=C_TEXT).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 10)
        )
        self.bd_username_var = tk.StringVar()
        ctk.CTkEntry(
            card, textvariable=self.bd_username_var,
            placeholder_text="Identifiant Bourse Direct",
            height=38, fg_color=C_BG, border_color=C_BORDER, text_color=C_TEXT,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 20), pady=(0, 10))

        ctk.CTkLabel(card, text="Mot de passe", font=ctk.CTkFont(size=13), text_color=C_TEXT).grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 16)
        )
        self.bd_password_var = tk.StringVar()
        ctk.CTkEntry(
            card, textvariable=self.bd_password_var, show="•",
            placeholder_text="••••••••",
            height=38, fg_color=C_BG, border_color=C_BORDER, text_color=C_TEXT,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 20), pady=(0, 16))

    def _card_trade_republic(self):
        card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=14, border_width=1, border_color=C_BORDER)
        card.grid(row=2, column=0, sticky="ew", padx=28, pady=(12, 0))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="TRADE REPUBLIC",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C_MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(16, 8))

        ctk.CTkLabel(card, text="Téléphone", font=ctk.CTkFont(size=13), text_color=C_TEXT).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 10)
        )
        self._tr_country_codes = {
            "🇫🇷 +33": "+33",
            "🇧🇪 +32": "+32",
            "🇨🇭 +41": "+41",
            "🇱🇺 +352": "+352",
            "🇩🇪 +49": "+49",
            "🇪🇸 +34": "+34",
            "🇮🇹 +39": "+39",
            "🇳🇱 +31": "+31",
            "🇵🇹 +351": "+351",
        }
        self.tr_country_var = tk.StringVar(value="🇫🇷 +33")
        phone_frame = ctk.CTkFrame(card, fg_color="transparent")
        phone_frame.grid(row=1, column=1, sticky="ew", padx=(0, 20), pady=(0, 10))
        phone_frame.columnconfigure(1, weight=1)
        ctk.CTkOptionMenu(
            phone_frame, variable=self.tr_country_var,
            values=list(self._tr_country_codes.keys()),
            width=110, height=38,
            fg_color=C_BG, button_color=C_BORDER, button_hover_color=C_MUTED,
            text_color=C_TEXT, dropdown_fg_color=C_BG, dropdown_text_color=C_TEXT,
        ).grid(row=0, column=0, padx=(0, 6))
        self.tr_phone_var = tk.StringVar()
        ctk.CTkEntry(
            phone_frame, textvariable=self.tr_phone_var,
            placeholder_text="0612345678",
            height=38, fg_color=C_BG, border_color=C_BORDER, text_color=C_TEXT,
        ).grid(row=0, column=1, sticky="ew")

        ctk.CTkLabel(card, text="Code PIN", font=ctk.CTkFont(size=13), text_color=C_TEXT).grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 10)
        )
        self.tr_pin_var = tk.StringVar()
        ctk.CTkEntry(
            card, textvariable=self.tr_pin_var, show="•",
            placeholder_text="4 chiffres",
            height=38, fg_color=C_BG, border_color=C_BORDER, text_color=C_TEXT,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 20), pady=(0, 10))

        sep = ctk.CTkFrame(card, height=1, fg_color=C_BORDER)
        sep.grid(row=3, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 10))

        self.tr_details_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(
            card, text="Récupérer les détails de chaque transaction (plus lent)",
            font=ctk.CTkFont(size=12), text_color=C_TEXT,
            variable=self.tr_details_var, onvalue=True, offvalue=False,
            progress_color=C_ACCENT,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 16))

    def _card_options(self):
        card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=14, border_width=1, border_color=C_BORDER)
        card.grid(row=3, column=0, sticky="ew", padx=28, pady=(12, 0))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="OPTIONS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C_MUTED,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 8))

        ctk.CTkLabel(card, text="Dossier export", font=ctk.CTkFont(size=13), text_color=C_TEXT).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 10)
        )
        self.output_dir_var = tk.StringVar(value=str(Path(__file__).parent / "exports"))
        ctk.CTkEntry(
            card, textvariable=self.output_dir_var,
            height=38, fg_color=C_BG, border_color=C_BORDER, text_color=C_TEXT,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 10))
        ctk.CTkButton(
            card, text="…", width=38, height=38,
            fg_color=C_CARD2, hover_color=C_BORDER, text_color=C_TEXT,
            corner_radius=8, command=self._choose_output_dir,
        ).grid(row=1, column=2, padx=(0, 20), pady=(0, 10))

        sep = ctk.CTkFrame(card, height=1, fg_color=C_BORDER)
        sep.grid(row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 10))

        self.headless_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(
            card, text="Chrome en arrière-plan (invisible)",
            font=ctk.CTkFont(size=12), text_color=C_TEXT,
            variable=self.headless_var, onvalue=True, offvalue=False,
            progress_color=C_ACCENT,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 16))

    def _card_journal(self):
        card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=14, border_width=1, border_color=C_BORDER)
        card.grid(row=5, column=0, sticky="nsew", padx=28, pady=(0, 8))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="JOURNAL",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=C_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 6))

        self.log_text = ctk.CTkTextbox(
            card, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=C_LOG_BG, text_color="#c8c8d4",
            wrap="word", state="disabled",
            corner_radius=10, border_width=1, border_color=C_BORDER,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self.log_text._textbox.tag_config("INFO",    foreground=C_SUCCESS)
        self.log_text._textbox.tag_config("WARNING", foreground=C_WARNING)
        self.log_text._textbox.tag_config("ERROR",   foreground=C_ERROR)
        self.log_text._textbox.tag_config("DEBUG",   foreground=C_MUTED)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _choose_output_dir(self):
        path = filedialog.askdirectory(initialdir=self.output_dir_var.get())
        if path:
            self.output_dir_var.set(path)

    def _append_log(self, level: str, msg: str):
        tag = level if level in ("INFO", "WARNING", "ERROR", "DEBUG") else "INFO"
        self.log_text.configure(state="normal")
        self.log_text._textbox.insert("end", msg + "\n", tag)
        self.log_text._textbox.see("end")
        self.log_text.configure(state="disabled")

    # ── Logging ──────────────────────────────────────────────────────────────

    def _setup_logging(self):
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    # ── Polling ──────────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                level, msg = self.log_queue.get_nowait()
                self._append_log(level, msg)
        except queue.Empty:
            pass

        try:
            req = self.code_request_queue.get_nowait()
            self._ask_code(req)
        except queue.Empty:
            pass

        self.after(100, self._poll)

    def _ask_code(self, req: dict):
        dialog = CodeDialog(self, title=req["title"], subtitle=req["subtitle"])
        code = dialog.get_result()
        self.code_response_queue.put(code)

    def _code_callback_factory(self, title: str):
        def _cb(prompt: str) -> str:
            self.code_request_queue.put({"title": title, "subtitle": prompt})
            return self.code_response_queue.get()
        return _cb

    # ── Run ──────────────────────────────────────────────────────────────────

    def _toggle_run(self):
        if self._running:
            self.status_var.set("Arrêt demandé — attente de la fin de l'opération…")
        else:
            self._start()

    def _start(self):
        # Demander validation pour chaque courtier
        export_bd = messagebox.askyesno(
            "Bourse Direct",
            "Souhaitez-vous exporter les actifs Bourse Direct ?",
        )
        export_tr = messagebox.askyesno(
            "Trade Republic",
            "Souhaitez-vous exporter les actifs Trade Republic ?",
        )

        if not export_bd and not export_tr:
            self.status_var.set("Aucun export sélectionné.")
            return

        # Validation des identifiants en fonction de la sélection
        if export_bd:
            if not self.bd_username_var.get().strip() or not self.bd_password_var.get().strip():
                messagebox.showwarning(
                    "Identifiants Bourse Direct manquants",
                    "Veuillez saisir votre login et mot de passe Bourse Direct.",
                )
                return
        if export_tr:
            if not self.tr_phone_var.get().strip() or not self.tr_pin_var.get().strip():
                messagebox.showwarning(
                    "Identifiants Trade Republic manquants",
                    "Veuillez saisir votre numéro de téléphone et code PIN Trade Republic.",
                )
                return

        output_dir = Path(self.output_dir_var.get())
        headless = self.headless_var.get()

        self._running = True
        self.run_btn.configure(
            text="⏹   En cours…",
            fg_color=C_CARD2, hover_color=C_BORDER,
        )
        self.status_var.set("Export en cours…")

        self._worker_thread = threading.Thread(
            target=self._run_exports,
            args=(export_bd, export_tr, output_dir, headless),
            daemon=True,
        )
        self._worker_thread.start()
        self.after(300, self._check_done)

    def _check_done(self):
        if self._worker_thread and not self._worker_thread.is_alive():
            self._on_done()
        else:
            self.after(300, self._check_done)

    def _on_done(self):
        self._running = False
        self.run_btn.configure(
            text="▶   Lancer l'export",
            fg_color=C_ACCENT, hover_color=C_ACCENT_H,
        )
        self.status_var.set("Terminé.")

    def _run_exports(self, export_bd: bool, export_tr: bool, output_dir: Path, headless: bool):
        results = []
        errors = []

        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")

        # ── Bourse Direct ──
        if export_bd:
            try:
                logging.info("── Démarrage export Bourse Direct ──")
                bd_output = output_dir / f"bourse_direct_positions_{date_str}.csv"
                with BourseDirectScraper(
                    self.bd_username_var.get().strip(),
                    self.bd_password_var.get().strip(),
                    headless=headless,
                    twofa_callback=self._code_callback_factory("Bourse Direct — Code SMS"),
                ) as scraper:
                    if scraper.login():
                        positions = scraper.get_all_positions()
                        bd_export_csv(positions, bd_output)
                        results.append(f"Bourse Direct : {bd_output.name}")
                    else:
                        errors.append("Bourse Direct : connexion échouée")
            except Exception as exc:
                logging.error(f"Erreur Bourse Direct : {exc}")
                errors.append(f"Bourse Direct : {exc}")

        # ── Trade Republic ──
        if export_tr:
            try:
                logging.info("── Démarrage export Trade Republic ──")
                country_prefix = self._tr_country_codes[self.tr_country_var.get()]
                local_number = self.tr_phone_var.get().strip().lstrip("0")
                full_phone = country_prefix + local_number
                tr_scraper = TradeRepublicScraper(
                    full_phone,
                    self.tr_pin_var.get().strip(),
                    twofa_callback=self._code_callback_factory("Trade Republic — Code 2FA"),
                    headless=headless,
                )
                if tr_scraper.login():
                    res = tr_scraper.export_all(
                        output_folder=output_dir,
                        extract_details=self.tr_details_var.get(),
                    )
                    results.append(f"Trade Republic : {Path(res['transactions_csv']).name}")
                else:
                    errors.append("Trade Republic : connexion échouée")
            except Exception as exc:
                logging.error(f"Erreur Trade Republic : {exc}")
                errors.append(f"Trade Republic : {exc}")

        # ── Résumé ──
        def _show_summary():
            if results and not errors:
                messagebox.showinfo(
                    "Export terminé",
                    "Exports réussis :\n\n" + "\n".join(f"• {r}" for r in results)
                    + f"\n\nDossier : {output_dir.resolve()}",
                )
            elif results and errors:
                messagebox.showwarning(
                    "Export partiel",
                    "Succès :\n" + "\n".join(f"• {r}" for r in results)
                    + "\n\nErreurs :\n" + "\n".join(f"• {e}" for e in errors),
                )
            else:
                messagebox.showerror(
                    "Erreur",
                    "Aucun export n'a abouti.\n\n" + "\n".join(f"• {e}" for e in errors),
                )

        self.after(0, _show_summary)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
