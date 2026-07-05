# Changelog — ha-can-gateway

## 2026-07-05

### feat: unified HA repo — add-on v0.6.0 + integration v3.4.0

- **Add-on `can_gateway/` v0.6.0:** Supervisor discovery, auto-deploy integracji, `homeassistant_api`, map `homeassistant_config:rw`.
- **Integracja `custom_components/can_gateway_v3/` v3.4.0:** HACS + ręczna instalacja z tego samego repo; bundel w `can_gateway/integration/can_gateway_v3/`.
- **`hacs.json`**, `repository.yaml`, README — jeden URL dla użytkowników HA.
- **Testy:** przeniesione z `can-control-suite` (`tests/test_can_gateway_v3_*`, `test_module_store`, `test_entity_export`).
