"""
Trade Republic Scraper - module refactorisé (callable depuis une GUI)

Expose :
  - TradeRepublicScraper : classe encapsulant l'authentification & l'export
  - export_transactions_csv / export_cash_csv : helpers autonomes
"""

import os
import json
import asyncio
import hashlib
import uuid
import base64
import time
import logging
from pathlib import Path

import websockets
import requests
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _headers_to_dict(response):
    extracted_headers = {}
    for header, header_value in response.headers.items():
        parsed_dict = {}
        entries = header_value.split(", ")
        for entry in entries:
            key_value = entry.split(";")[0]
            if "=" in key_value:
                key, value = key_value.split("=", 1)
                parsed_dict[key.strip()] = value.strip()
        extracted_headers[header] = parsed_dict if parsed_dict else header_value
    return extracted_headers


def _flatten_and_clean_json(all_data, sep="."):
    all_keys: dict = {}
    flattened_data = []

    def flatten(nested_json, parent_key=""):
        flat_dict = {}
        for key, value in nested_json.items():
            new_key = f"{parent_key}{sep}{key}" if parent_key else key
            if isinstance(value, dict):
                flat_dict.update(flatten(value, new_key))
            else:
                flat_dict[new_key] = value
            all_keys[new_key] = None
        return flat_dict

    for item in all_data:
        flattened_data.append(flatten(item))

    return [{key: item.get(key, None) for key in all_keys} for item in flattened_data]


def _transform_data_types(df):
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.strftime("%d/%m/%Y")

    amount_columns = [
        "amount.value", "amount.fractionDigits",
        "subAmount.value", "subAmount.fractionDigits",
    ]
    for col in amount_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].apply(lambda x: str(x).replace(".", ",") if pd.notna(x) else x)
    return df


def _generate_device_info():
    device_id = hashlib.sha512(uuid.uuid4().bytes).hexdigest()
    return base64.b64encode(json.dumps({"stableDeviceId": device_id}).encode()).decode()


def _get_waf_token_with_selenium(headless: bool = True) -> str:
    log.info("Récupération du token WAF via Selenium…")
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    try:
        driver = webdriver.Chrome(options=options)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
        driver.get("https://app.traderepublic.com/")
        time.sleep(5)

        waf_token = None
        for cookie in driver.get_cookies():
            if "aws-waf-token" in cookie.get("name", ""):
                waf_token = cookie["value"]
                break

        if not waf_token:
            try:
                waf_token = driver.execute_script(
                    "return window.AWSWafIntegration && window.AWSWafIntegration.getToken();"
                )
            except Exception:
                pass

        driver.quit()

        if waf_token:
            log.info("Token WAF récupéré.")
            return waf_token
        log.warning("Token WAF introuvable.")
        return ""
    except Exception as e:
        log.error(f"Erreur Selenium (WAF) : {e}")
        return ""


def _parse_ws_response(response: str, fallback: str = "{}"):
    open_ch, close_ch = ("[", "]") if fallback == "[]" else ("{", "}")
    start = response.find(open_ch)
    end = response.rfind(close_ch)
    return json.loads(response[start:end + 1] if start != -1 and end != -1 else fallback)


# ── WebSocket helpers ─────────────────────────────────────────────────────────

async def _connect_ws():
    ws = await websockets.connect("wss://api.traderepublic.com")
    locale_config = {
        "locale": "fr",
        "platformId": "webtrading",
        "platformVersion": "safari - 18.3.0",
        "clientId": "app.traderepublic.com",
        "clientVersion": "3.151.3",
    }
    await ws.send(f"connect 31 {json.dumps(locale_config)}")
    await ws.recv()
    log.info("Connexion WebSocket Trade Republic OK.")
    return ws


async def _fetch_transaction_details(ws, transaction_id, token, message_id):
    payload = {"type": "timelineDetailV2", "id": transaction_id, "token": token}
    message_id += 1
    await ws.send(f"sub {message_id} {json.dumps(payload)}")
    response = await ws.recv()
    await ws.send(f"unsub {message_id}")
    await ws.recv()

    response_data = _parse_ws_response(response)
    transaction_data = {}
    for section in response_data.get("sections", []):
        if section.get("title") == "Transaction":
            for item in section.get("data", []):
                header = item.get("title")
                value = item.get("detail", {}).get("text")
                if header and value:
                    transaction_data[header] = value
    return transaction_data, message_id


async def _fetch_all_transactions(token, extract_details, output_folder: Path):
    all_data = []
    message_id = 0

    async with await _connect_ws() as websocket:
        after_cursor = None
        while True:
            payload = {"type": "timelineTransactions", "token": token}
            if after_cursor:
                payload["after"] = after_cursor
            message_id += 1
            await websocket.send(f"sub {message_id} {json.dumps(payload)}")
            response = await websocket.recv()
            await websocket.send(f"unsub {message_id}")
            await websocket.recv()

            data = _parse_ws_response(response)

            if not data.get("items"):
                break

            if extract_details:
                for transaction in data["items"]:
                    tid = transaction.get("id")
                    if tid:
                        details, message_id = await _fetch_transaction_details(
                            websocket, tid, token, message_id
                        )
                        transaction.update(details)
                    all_data.append(transaction)
            else:
                all_data.extend(data["items"])

            after_cursor = data.get("cursors", {}).get("after")
            if not after_cursor:
                break

    investment_event_types = {
        "SPARE_CHANGE_AGGREGATE",
        "SSP_CORPORATE_ACTION_CASH",
        "TRADING_SAVINGSPLAN_EXECUTED",
        "TRADING_TRADE_EXECUTED",
        "SAVINGS_PLAN_EXECUTED",
        "SAVINGS_PLAN_INVOICE_CREATED",
        "ORDER_EXECUTED",
        "TRADE_INVOICE",
        "PEA_DEPOSIT_DEBIT",
        "PEA_WITHDRAWAL_CASH",
    }
    all_data = [t for t in all_data if t.get("eventType") in investment_event_types]

    flattened_data = _flatten_and_clean_json(all_data)
    output_path = output_folder / "trade_republic_transactions.csv"
    if flattened_data:
        df = pd.DataFrame(flattened_data)
        df = df.dropna(axis=1, how="all")
        df = _transform_data_types(df)
        df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
        log.info(f"✓ CSV transactions exporté : {output_path}")
    else:
        log.warning("Aucune transaction à exporter.")
    return output_path, len(all_data)


async def _fetch_cash(token, output_folder: Path):
    async with await _connect_ws() as websocket:
        payload = {"type": "availableCash", "token": token}
        await websocket.send(f"sub 1 {json.dumps(payload)}")
        response = await websocket.recv()

        response_data = _parse_ws_response(response, fallback="[]")
        output_path = output_folder / "trade_republic_profile_cash.csv"
        flattened = _flatten_and_clean_json(response_data)
        if flattened:
            df = pd.DataFrame(flattened)
            df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
            log.info(f"✓ CSV cash exporté : {output_path}")
        return output_path


# ── Classe principale ─────────────────────────────────────────────────────────

class TradeRepublicScraper:
    """
    Scraper Trade Republic. Fournit un callback de saisie 2FA.

    Usage :
        scraper = TradeRepublicScraper(phone_number, pin, twofa_callback=callable)
        scraper.login()
        scraper.export_all(output_folder=Path("exports"), extract_details=True)
    """

    LOGIN_URL = "https://api.traderepublic.com/api/v1/auth/web/login"

    def __init__(self, phone_number: str, pin: str, twofa_callback=None, headless: bool = True):
        # Normalize phone number: remove spaces/dashes, ensure leading +
        normalized = phone_number.strip().replace(" ", "").replace("-", "").replace(".", "")
        if not normalized.startswith("+"):
            normalized = "+" + normalized
        self.phone_number = normalized
        self.pin = pin
        self.headless = headless
        self._twofa_callback = twofa_callback or input
        self.session_token: str | None = None
        self._headers: dict = {}

    def login(self) -> bool:
        device_info = _generate_device_info()
        waf_token = _get_waf_token_with_selenium(self.headless)

        self._headers = {
            "Accept": "*/*",
            "Accept-Language": "fr",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "x-aws-waf-token": waf_token,
            "x-tr-app-version": "13.40.5",
            "x-tr-device-info": device_info,
            "x-tr-platform": "web",
        }

        log.info("Connexion à l'API Trade Republic…")
        login_response = requests.post(
            self.LOGIN_URL,
            json={"phoneNumber": self.phone_number, "pin": self.pin},
            headers=self._headers,
        )

        if login_response.status_code != 200:
            log.error(f"Erreur API Trade Republic (HTTP {login_response.status_code}) : {login_response.text}")
            return False

        try:
            login_data = login_response.json()
        except ValueError:
            log.error("Réponse API non JSON.")
            return False

        process_id = login_data.get("processId")
        countdown = login_data.get("countdownInSeconds")
        if not process_id:
            log.error("Échec de l'initialisation de la connexion. Vérifiez vos identifiants.")
            return False

        prompt = f"Entrez le code 2FA reçu ({countdown}s restants)"
        code = self._twofa_callback(prompt).strip()

        if code.upper() == "SMS":
            requests.post(
                f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/resend",
                headers=self._headers,
            )
            code = self._twofa_callback("Entrez le code 2FA reçu par SMS").strip()

        verify_response = requests.post(
            f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/{code}",
            headers=self._headers,
        )

        if verify_response.status_code != 200:
            log.error(f"Échec de la vérification (HTTP {verify_response.status_code}) : {verify_response.text}")
            return False

        response_headers = _headers_to_dict(verify_response)
        self.session_token = response_headers.get("Set-Cookie", {}).get("tr_session")

        if not self.session_token:
            log.error("Token de session introuvable.")
            return False

        log.info("Connexion Trade Republic réussie.")
        return True

    def export_all(self, output_folder: Path, extract_details: bool = True) -> dict:
        if not self.session_token:
            raise RuntimeError("Non authentifié. Appelez login() d'abord.")

        output_folder = Path(output_folder)
        output_folder.mkdir(parents=True, exist_ok=True)

        async def _run_all():
            tx = await _fetch_all_transactions(self.session_token, extract_details, output_folder)
            cash = await _fetch_cash(self.session_token, output_folder)
            return tx, cash

        (tx_path, count), cash_path = asyncio.run(_run_all())

        return {
            "transactions_csv": tx_path,
            "cash_csv": cash_path,
            "transactions_count": count,
        }
