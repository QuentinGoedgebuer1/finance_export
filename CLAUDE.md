# CLAUDE.md

Ce fichier fournit des indications à Claude Code (claude.ai/code) pour travailler dans ce dépôt.

## Lancer l'application

```bash
pip install -r requirements.txt
python gui.py
```

Sous Windows : `launch_gui.bat` (double-clic ou terminal).

Il n'y a pas de tests ni de configuration de linter.

## Architecture

Trois modules, pas de package partagé — tous les imports sont directs entre fichiers.

**`gui.py`** — Interface customtkinter, point d'entrée principal. Les scrapers tournent dans un thread worker `daemon` pour ne pas bloquer l'UI. La communication inter-threads passe par trois queues :
- `log_queue` : logs des scrapers → panneau journal de l'UI (interrogée toutes les 100ms via `self.after`)
- `code_request_queue` / `code_response_queue` : le thread worker envoie une demande de code 2FA → la modale `CodeDialog` s'ouvre sur le thread principal → le code revient au worker. Le worker est bloqué sur `code_response_queue.get()` jusqu'à la saisie.

**`bourse_direct_scraper.py`** — Scraper Selenium. Se connecte à `boursedirect.fr`, gère une modale 2FA SMS à 6 chiffres (deux stratégies : envoi groupé sur le premier champ, puis chiffre par chiffre en fallback), puis scrape les éléments `.position-row` de la page React du portefeuille. `build_driver()` crée une instance Chrome furtive (flags anti-bot). `safe_float()` convertit les chaînes numériques au format français (`"1 234,56 €"`) en float.

**`trade_republic_scraper.py`** — Client REST + WebSocket. Flow d'authentification : POST sur `/api/v1/auth/web/login` → réception du `processId` → saisie du code 2FA → POST sur `…/{processId}/{code}` → extraction du cookie `tr_session`. Les données sont récupérées via `wss://api.traderepublic.com` avec un protocole numéroté sub/unsub (`sub {id} {payload}` / `unsub {id}`). Une instance Chrome Selenium est lancée uniquement pour obtenir le cookie AWS WAF. `export_all()` exécute deux coroutines async séquentiellement (pagination des transactions + cash) dans un seul `asyncio.run()`.

## Conventions importantes

- Les sélecteurs CSS Bourse Direct sont centralisés dans le dict `SELECTORS` en haut de `bourse_direct_scraper.py` — à mettre à jour en cas de changement du site.
- Le set `investment_event_types` dans `_fetch_all_transactions` contrôle quels types d'événements Trade Republic sont conservés dans le CSV — à étendre pour de nouveaux types de transactions.
- `twofa_callback` sur les deux scrapers suit le protocole `input()` : appelé avec une chaîne de prompt, retourne le code saisi. La GUI injecte son propre callback via `_code_callback_factory` ; le fallback par défaut est `input` (usage CLI).
- Les CSV de sortie utilisent `;` comme séparateur et l'encodage `utf-8-sig` (compatibilité Excel).
