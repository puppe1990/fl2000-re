# Handoff — fl2000-re

Resumo do estado do projeto, do que foi medido no hardware e do que falta. Leia junto com
`AGENTS.md` (regras) e `README.md` (uso).

## Objetivo da missão

Deixar a imagem no monitor ligado ao **Hagibis FL2000DX** (`1d5c:2000`) **nítida/legível**. O
`mirror` saiu borrado e o `extend` saiu grande/esticado; investigamos como melhorar.

## Hardware de teste

- MacBook Air M2 (Mac14,15), macOS 14.5.
- Dongle: Hagibis FL2000DX + transmissor IT66121, USB 2.0.
- Monitores: interno 2880x1864 retina; **Dell P2219H** 1920x1080; **Dell P2016** 1440x900.
- Saída do Hagibis testada de duas formas:
  - HDMI → **adaptador HDMI→VGA** → P2016.
  - HDMI → **direto** no P2219H (sem adaptador).

## Resultado medido (o essencial)

**Só 640x480@60 RGB565 trava.** Todos os modos maiores falharam:

| modo                                 | resultado                                            |
| ------------------------------------ | ---------------------------------------------------- |
| 640x480 RGB565                       | estável, 60 fps                                      |
| 720x480 RGB565 (CEA 480p)            | chiplimpa mas o P2219H rejeita timing de TV          |
| 800x600 RGB565/RGB332                | não trava (precisa 40 MHz; o FL2000 vai até ~27 MHz) |
| 640x400 / 720x450 / 640x360 (custom) | perdem sincronia (o P2219H rejeita modos custom)     |

Outros achados:

- **Throughput real ~55 MB/s** (90 fps de frame de 614 KB), bem acima do budget de 42 MB/s assumido
  em `video_modes.py`.
- **HPD = NAO sempre** — inclusive no HDMI direto do P2219H. O dongle provavelmente não fornece +5V
  no HDMI, então o IT66121 nunca enxerga sink: sem HPD, sem EDID/DDC. O sinal é forçado.
- **`lbuf_ovf`** latcha a cada frame durante streaming — é o comportamento normal (o driver Linux de
  referência trata como recuperável e só limpa). O reset real é `REG_ACLK` bit 20 (`lbuf_sw_rst`),
  não escrever no `REG_STATUS` (write-back não limpa neste chip). Ver `monitor-log`.
- **`CGVirtualDisplay`**: o helper precisa anunciar **um único modo**; listar vários faz o
  WindowServer escolher o maior e ignorar o pedido. Também é preciso `select_mode` explícito via
  `CGConfigureDisplayWithDisplayMode`.
- **Nunca enumerar arrays CF do CoreGraphics por `ctypes`** — `CFArrayGetCount` dá segfault com
  ponteiro stale. Use o helper ObjC ou `system_profiler`. (Registrado no `AGENTS.md`.)

**Conclusão:** o FL2000 é, na prática, um dispositivo **640x480**. Num painel 900p/1080p a imagem
será ampliada (borrada) e ainda vai esticar (Wide) ou dar barras (4:3). Não há solução por software
com esse hardware; o único upgrade real seria um adaptador/monitor que aceite 720p/1080p direto.

## Estado do código

- Pacote `fl2000_re/` (um módulo por responsabilidade) + helper ObjC `native/`.
- Comandos: `dump`, `detect`, `edid`, `bars`, `mirror`, `extend`, `monitor-log`.
- Flags: `--seconds`, `--monitor`, `--underscan`, `--stretch-x`, `--mode`, `--v-shift`,
  `--place left|right`, `--out`.
- Perfil do P2016: `--mode 640x480 --underscan 1.0 --stretch-x 1.1585` (mirror) e `stretch-x 1.0`
  (extend, 1:1).
- `bin/extend` e `bin/mirror` já travam o perfil estável (640x480) e `--place left`.
- `extend` agora: tela virtual 640x480 em `x=-640` (mais à esquerda), 1:1, FL2000 640x480 estável.
- 137 testes; `make check` = ruff + pytest + prettier; CI verde (GitHub Actions).
- PRs mergeados #10–#17 (perfil P2016, testes de hardware, `--place`, `monitor-log`, helper/vsync).

## Pendências / próximos passos

1. **Cursor no `extend`**: foi implementado `screencapture -C` (`capture.py`) para incluir o
   ponteiro, mas o usuário **não viu o cursor** no teste. Não confirmado; investigar se `-C` vale
   com `-R`, permissão de gravação de tela, ou se o cursor não estava sobre a tela virtual na hora.
2. **Aspecto**: com saída 4:3 e painel 16:10/16:9, escolher no OSD do monitor **Wide** (preenche,
   ~20% largo) ou **4:3** (proporcional, com barras).
3. **HPD/EDID**: sem HPD não dá pra negociar modos. Só resolve trocando o dongle/adaptador por um
   com HDMI que passe HPD/EDID.
4. Mirror de tela inteira continua borrado (downscale da tela retina) — inerente.

## Gotchas operacionais

- Matar por **PID exato**; nunca `pkill -f hagibis_re.py`.
- Matar o processo Python **deixa o helper da tela virtual pendurado** — mate
  `hagibis_virtual_display` também, senão o próximo `extend` falha ao criar a tela.
- `Access denied` / claim fail: desplugar o USB-A do Hagibis e reconectar; `diskutil unmountDisk` o
  fake CD se remontar.
- Diagnóstico sempre com `python -u` (senão um crash engole a saída).
- `REG_ACLK` bit 28 (EOF = ZLP) precisa ficar setado.
