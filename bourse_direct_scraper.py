"""
Bourse Direct Portfolio Scraper
Extrait les positions actuelles (toutes lignes + valorisation) et génère un CSV.
"""

import csv
import time
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, fields

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


@dataclass
class Position:
    compte: str
    libelle: str
    isin: str
    quantite: float
    cours: float
    devise: str
    valorisation_eur: float
    prix_revient_unitaire: float
    plus_value_latente: float
    plus_value_pct: float
    date_extraction: str


# ─── Sélecteurs CSS ──────────────────────────────────────────────────────────
# Ces sélecteurs correspondent à l'interface Bourse Direct au moment de
# l'écriture du script. Si le site change, ajuste-les ici.

SELECTORS = {
    "login_id":       "#bd_auth_login_type_login",
    "login_password": "#bd_auth_login_type_password",
    "login_submit":   "[data-testid='button-submit'], button[type='submit'], input[type='submit']",
    "portfolio_link": "a[href*='portefeuille'], a[href*='portfolio']",
    "account_tabs":   ".account-tab, .tab-compte, [data-account]",
    "positions_table":"table.positions, table.portefeuille, .grid-positions",
    "position_rows":  "tr.position-row, tr[data-isin], tbody tr",
}


def build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def wait_for(driver, by, selector, timeout=15):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, selector))
    )


def safe_float(text: str) -> float:
    """Convertit une chaîne de type '1 234,56' en float."""
    if not text:
        return 0.0
    cleaned = (
        text.strip()
        .replace("\xa0", "")
        .replace(" ", "")
        .replace("€", "")
        .replace("%", "")
        .replace("+", "")
        .replace(",", ".")
    )
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class BourseDirectScraper:
    BASE_URL = "https://www.boursedirect.fr"
    LOGIN_URL = "https://www.boursedirect.fr/fr/login"
    PORTFOLIO_URL = "https://www.boursedirect.fr/fr/mon-compte/portefeuilles"

    def __init__(self, username: str, password: str, headless: bool = True, twofa_callback=None):
        self.username = username
        self.password = password
        self.headless = headless
        self.driver: webdriver.Chrome | None = None
        self._twofa_callback = twofa_callback or input

    def __enter__(self):
        self.driver = build_driver(self.headless)
        return self

    def __exit__(self, *_):
        if self.driver:
            self.driver.quit()

    # ── Authentification ──────────────────────────────────────────────────

    def login(self) -> bool:
        log.info("Connexion à Bourse Direct…")
        self.driver.get(self.LOGIN_URL)
        time.sleep(2)

        try:
            # Accepter les cookies si la bannière est présente
            for btn in self.driver.find_elements(By.TAG_NAME, "button"):
                try:
                    txt = btn.text.strip()
                    if "accepter" in txt.lower() and btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        log.info(f"Cookies acceptés via : '{txt}'")
                        time.sleep(1)
                        break
                except Exception:
                    pass

            # Remplir le formulaire
            id_field = wait_for(self.driver, By.CSS_SELECTOR, SELECTORS["login_id"])
            id_field.clear()
            id_field.send_keys(self.username)

            pwd_field = self.driver.find_element(By.CSS_SELECTOR, SELECTORS["login_password"])
            pwd_field.clear()
            pwd_field.send_keys(self.password)

            submit = self.driver.find_element(By.CSS_SELECTOR, SELECTORS["login_submit"])
            self.driver.execute_script("arguments[0].click();", submit)
            time.sleep(5)

            # Gérer le code de vérification 2FA si demandé
            if self._handle_2fa():
                log.info("En attente de la redirection post-2FA…")
                try:
                    WebDriverWait(self.driver, 30).until(
                        lambda d: "login" not in d.current_url.lower()
                    )
                except TimeoutException:
                    log.error("Redirection post-2FA trop longue.")
                    return False

            # Vérifier la connexion
            if "login" in self.driver.current_url.lower():
                log.error("Échec de connexion.")
                return False

            log.info("Connexion réussie.")
            return True

        except TimeoutException:
            log.error("Timeout — page de login non trouvée.")
            log.debug(f"URL au moment du timeout : {self.driver.current_url}")
            return False

    def _handle_2fa(self) -> bool:
        """
        Détecte la modal 2FA de Bourse Direct (6 cases input[type='number'])
        et saisit le code SMS chiffre par chiffre.
        Retourne True si un code a été soumis, False si pas de 2FA détecté.
        """
        # Les 6 cases OTP sont des input[type='number'] visibles sans name
        otp_fields = [
            i for i in self.driver.find_elements(By.CSS_SELECTOR, "input[type='number']")
            if i.is_displayed()
        ]

        if not otp_fields:
            return False

        log.info(f"Code de vérification demandé — {len(otp_fields)} case(s) détectée(s).")
        code = self._twofa_callback(">>> Entrez le code de vérification reçu (ex: 123456) : ").strip()

        # Méthode 1 : envoyer tout le code depuis le premier champ (auto-distribution)
        otp_fields[0].click()
        time.sleep(0.2)
        otp_fields[0].send_keys(code)
        time.sleep(0.5)

        # Vérifier si les champs ont été remplis, sinon méthode 2 : chiffre par chiffre
        filled = sum(1 for f in otp_fields if f.get_attribute("value"))
        if filled < len(otp_fields):
            log.info("Auto-distribution échouée, saisie chiffre par chiffre…")
            for i, field in enumerate(otp_fields):
                if i < len(code):
                    field.click()
                    field.clear()
                    field.send_keys(code[i])
                    time.sleep(0.15)

        time.sleep(0.5)

        # Sélectionner "Non" pour "Faire confiance à cet appareil"
        radios = [r for r in self.driver.find_elements(By.CSS_SELECTOR, "input[type='radio']") if r.is_displayed()]
        selected = False
        for radio in radios:
            try:
                rid = radio.get_attribute("id")
                label_text = ""
                if rid:
                    try:
                        label = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{rid}']")
                        label_text = label.text.strip().lower()
                    except NoSuchElementException:
                        pass
                val = (radio.get_attribute("value") or "").lower()
                if "non" in label_text or "non" in val or "no" in val:
                    self.driver.execute_script("arguments[0].click();", radio)
                    selected = True
                    break
            except Exception:
                pass
        if not selected and radios:
            self.driver.execute_script("arguments[0].click();", radios[-1])

        time.sleep(0.3)

        # Cliquer sur "Continuer"
        submitted = False
        for btn in self.driver.find_elements(By.TAG_NAME, "button"):
            try:
                if btn.is_displayed() and btn.text.strip().lower() == "continuer":
                    self.driver.execute_script("arguments[0].click();", btn)
                    log.info("2FA : bouton 'Continuer' cliqué")
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            otp_fields[-1].send_keys(Keys.RETURN)
            log.info("2FA : soumission via ENTER sur le dernier champ")

        log.info("Code soumis.")
        return True

    # ── Navigation & extraction ───────────────────────────────────────────

    def _close_modals(self):
        """Ferme les modals de bienvenue/découverte si présentes."""
        for btn in self.driver.find_elements(By.TAG_NAME, "button"):
            try:
                txt = btn.text.strip().lower()
                if txt in ("fermer", "passer", "ignorer", "skip", "close", "×", "x") and btn.is_displayed():
                    self.driver.execute_script("arguments[0].click();", btn)
                    log.info(f"Modal fermée (bouton : '{btn.text.strip()}')")
                    time.sleep(1)
            except Exception:
                pass

    def get_all_positions(self) -> list[Position]:
        log.info("Navigation vers le portefeuille…")
        self.driver.get(self.PORTFOLIO_URL)
        time.sleep(3)

        self._close_modals()

        positions: list[Position] = []
        date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Détecter les comptes disponibles (PEA, CTO, etc.)
        account_names = self._get_account_names()
        log.info(f"Comptes détectés : {account_names or ['compte principal']}")

        if account_names:
            for account in account_names:
                log.info(f"Extraction du compte : {account}")
                positions.extend(self._extract_positions(account, date_now))
        else:
            positions.extend(self._extract_positions("Principal", date_now))

        log.info(f"{len(positions)} lignes extraites au total.")
        return positions

    def _get_account_names(self) -> list[str]:
        """Retourne la liste des onglets/comptes disponibles."""
        try:
            tabs = self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["account_tabs"])
            return [t.text.strip() for t in tabs if t.text.strip()]
        except Exception:
            return []

    def _extract_positions(self, account_name: str, date_now: str) -> list[Position]:
        """Parse les lignes .position-row de l'interface React de Bourse Direct."""
        positions = []
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".position-row"))
            )
            rows = self.driver.find_elements(By.CSS_SELECTOR, ".position-row")
            log.info(f"  → {len(rows)} lignes trouvées")
            for row in rows:
                pos = self._parse_row(row, account_name, date_now)
                if pos:
                    positions.append(pos)
        except TimeoutException:
            log.warning(f"Aucune ligne .position-row trouvée pour '{account_name}'.")
        return positions

    def _parse_row(self, row, account_name: str, date_now: str) -> Position | None:
        """
        Extrait une Position depuis un élément .position-row (interface React).
        Structure du texte consolidé :
          Quantité
          PRU : X.XX €
          +/- value %
          Valorisation €
          +/- value €
          Poids%
        """
        def sel(css):
            try:
                return row.find_element(By.CSS_SELECTOR, css).text.strip()
            except NoSuchElementException:
                return ""

        try:
            libelle = sel("[class*='_name_']")
            if not libelle:
                return None

            # Cours et devise : "83.37 EUR"
            cours_txt = sel("[class*='_last_']")
            cours_parts = cours_txt.split()
            cours = safe_float(cours_parts[0]) if cours_parts else 0.0
            devise = cours_parts[1] if len(cours_parts) > 1 else "EUR"

            # Bloc consolidé : "25\nPRU : 87.31 €\n-4.51 %\n2 084.25 €\n-98.53 €\n68%"
            content = sel("[class*='_content_']")
            lines = [l.strip() for l in content.splitlines() if l.strip()]

            quantite        = safe_float(lines[0]) if len(lines) > 0 else 0.0
            pru_txt         = lines[1] if len(lines) > 1 else ""
            pru             = safe_float(pru_txt.replace("PRU :", "").replace("PRU:", ""))
            pv_pct          = safe_float(lines[2]) if len(lines) > 2 else 0.0
            valorisation    = safe_float(lines[3]) if len(lines) > 3 else 0.0
            pv_latente      = safe_float(lines[4]) if len(lines) > 4 else 0.0

            return Position(
                compte=account_name,
                libelle=libelle,
                isin="",
                quantite=quantite,
                cours=cours,
                devise=devise,
                valorisation_eur=valorisation,
                prix_revient_unitaire=pru,
                plus_value_latente=pv_latente,
                plus_value_pct=pv_pct,
                date_extraction=date_now,
            )
        except Exception as e:
            log.debug(f"Ligne ignorée : {e}")
            return None


# ── Export CSV ────────────────────────────────────────────────────────────────

def export_csv(positions: list[Position], output_path: Path) -> None:
    if not positions:
        log.warning("Aucune position à exporter.")
        return

    pos_fields = fields(Position)
    headers = [f.name for f in pos_fields]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        for pos in positions:
            writer.writerow({f.name: getattr(pos, f.name) for f in pos_fields})

    total = sum(p.valorisation_eur for p in positions)
    pv_total = sum(p.plus_value_latente for p in positions)
    log.info(f"✓ CSV exporté : {output_path}")
    log.info(f"  Valorisation totale : {total:,.2f} €")
    log.info(f"  +/- value latente   : {pv_total:+,.2f} €")
    log.info(f"  Nombre de lignes    : {len(positions)}")
