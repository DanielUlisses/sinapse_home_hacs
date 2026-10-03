# Sinapse Home para Home Assistant

Integração (custom component, instalável via HACS) para aquecedores e controladores de spa/hidromassagem da **Sinapse Industrial** que usam o app **Sinapse Home**.

O app Sinapse Home é uma versão com a marca da Sinapse do ESP RainMaker, da Espressif. Esta integração fala com a mesma API de nuvem que o app usa, com o login da sua conta.

## Entidades

As entidades são criadas a partir da configuração que o próprio equipamento informa. Num **Sense Duo**:

| Equipamento no app | Entidade no HA |
|---|---|
| Painel, Hidro 1, Borbulhador | `switch` |
| Aquecedor | `climate` (temperatura programada 20–40 °C, ação aquecendo/ociosa) |
| Aquecedor · Temperatura | `sensor` (°C, com histórico) |
| Aquecedor · Status / Nível / Refrigerando | `binary_sensor` |
| Cromoterapia | `light` (liga/desliga, brilho, cor HS) |
| Cromoterapia · Efeitos | `button` (avança o efeito) |

Outros modelos devem funcionar sem mudanças: parâmetros desconhecidos viram `binary_sensor`, `sensor` ou `button` conforme o tipo.

## Instalação

1. HACS → menu ⋮ → **Repositórios personalizados** → `https://github.com/DanielUlisses/sinapse_home_hacs`, categoria **Integração**.
2. Instale **Sinapse Home** e reinicie o Home Assistant.
3. **Configurações → Dispositivos e serviços → Adicionar integração → Sinapse Home**, com o mesmo e-mail e senha do app.

## Funcionamento

- Polling na nuvem a cada 30 s. Depois de um comando, o estado muda na hora e é confirmado com uma nova leitura 3 s depois.
- Sessão renovada automaticamente com o refresh token. Se a senha mudar, o HA pede reautenticação.
- `iot_class: cloud_polling`: sem internet, o controle pelo HA para (o painel físico continua funcionando).

## Roadmap

- [ ] Controle local (`esp_local_ctrl` via mDNS `_esp_local_ctrl._tcp`, Security 1 com o POP obtido da nuvem), usando a nuvem só como fallback.
- [ ] Opção de intervalo de polling.

## Ferramentas

`tools/sinapse_discovery.py` faz login, lista os nodes da conta e mostra todos os parâmetros (somente leitura). Útil para mapear modelos novos. O JSON gerado contém o POP de controle local, então não publique.

## Aviso

Projeto independente, sem relação com a Sinapse Industrial ou a Espressif. Use por sua conta e risco.
