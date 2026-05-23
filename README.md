# CAN Gateway — dodatek Home Assistant

Prywatne repozytorium dodatku Supervisora (USB-CAN, panel web, REST).

## Instalacja w HA OS

1. **Ustawienia → Apps → App store → ⋮ → Repositories**
2. URL (repo publiczne):
   `https://github.com/arturkmat/ha-can-gateway`
3. Odswiez sklep → zainstaluj **CAN Gateway**

### Repo prywatne

Supervisor nie klonuje prywatnego GitHub bez tokenu. Opcje:

- **Zalecane:** kopia lokalna przez Samba do `/addons/can_gateway/` (bez URL)
- **Alternatywa:** tymczasowo ustaw repo na **Public** (tylko ten addon, bez kluczy)
- Integracja HA: recznie `custom_components/can_gateway_v2/` z glownego repo `can-control-suite`

## Integracja (encje)

Skopiuj z repo `can-control-suite`:
`home_assistant/custom_components/can_gateway_v2/` → `/config/custom_components/`

Tryb integracji: **Dodatek HA (zalecane)**.
