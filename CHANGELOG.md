# Changelog — ha-can-gateway

## 2026-07-05 (v0.6.1)

### fix: scan-only add-on UI + MASTER_KEY graceful scan + auto entity import

- **Panel Ingress:** uproszczony do skanu magistrali (status, tabela modułów, komunikat o braku `master_key_hex`).
- **`configurator_engine.scan_modules_sync`:** discovery GET_SUMMARY bez klucza; aktywny odczyt GPIO/relay pomijany bez MASTER_KEY; skan nie pada na wyjątku relay refresh.
- **`/api/discovery`:** pole `discovery_version` (inkrement przy zapisie `modules.json`); integracja nasłuchuje zmian.
- **`can_gateway_v3` (add-on):** auto-import encji z `/api/state` po skanie; tworzenie button/cover z `summary` bez pełnego runtime.
- **`deploy_integration.sh`:** poprawne wywołanie `homeassistant.reload_custom_components` przez Supervisor API.
- **Stabilność:** brak auto-skanu startowego bez MASTER_KEY; `POST /api/scan` opakowany w try/except.
- **Domyślny bitrate:** 125000 (zgodnie z protokołem CAN).
- **Testy:** `tests/test_scan_without_master_key.py`.

## 2026-07-05

### feat: unified HA repo — add-on v0.6.0 + integration v3.4.0

- **Add-on `can_gateway/` v0.6.0:** Supervisor discovery, auto-deploy integracji, `homeassistant_api`, map `homeassistant_config:rw`.
- **Integracja `custom_components/can_gateway_v3/` v3.4.0:** HACS + ręczna instalacja z tego samego repo; bundel w `can_gateway/integration/can_gateway_v3/`.
- **`hacs.json`**, `repository.yaml`, README — jeden URL dla użytkowników HA.
- **Testy:** przeniesione z `can-control-suite` (`tests/test_can_gateway_v3_*`, `test_module_store`, `test_entity_export`).
