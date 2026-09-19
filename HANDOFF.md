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
- **Nunca chamar `CGImageGetWidth` / `CGWindowListCreateImage` no processo do USB** — `mss.grab` faz
  as duas; um `CGImage` inválido é SIGSEGV (`0x39e37920`, 2026-09-19), não Python exception. Preview
  via `screencapture` (subprocess). Captura ao vivo no filho; se o filho morre, o HDMI fica no
  último frame e o log imprime `capture_worker_exit_message`.
- Dongle enumerado a **480 Mb/s** atrás de um hub USB 2.0 (VIA Labs). USB 3 / 1080p no mesmo chip
  não se aplica: o Hagibis do usuário é USB 2.

**Conclusão:** o FL2000 em USB 2 é, na prática, um dispositivo **640x480**. Num painel 900p/1080p a
imagem será ampliada. Downscale de um desktop maior (B/C/HiDPI) deixa a UI no tamanho certo e borra
o texto. O usuário ficou no **A** (nítido, UI grande). Upgrade real: outro dongle (USB 3 /
DisplayLink) ou o HDMI nativo do Mac (P2219H).

## Estado do código

- Pacote `fl2000_re/` (um módulo por responsabilidade) + helper ObjC `native/`.
- Comandos: `dump`, `detect`, `edid`, `bars`, `mirror`, `extend`, `monitor-log`.
- Flags: `--seconds`, `--monitor`, `--underscan`, `--stretch-x`, `--mode`, `--v-shift`,
  `--place left|right`, `--out`, `--cursor` / `--no-cursor` (default ligado no extend).
- Perfil do P2016: `--mode 640x480 --underscan 1.0 --stretch-x 1.1585` (mirror) e `stretch-x 1.0`
  (extend, 1:1). `bin/extend` encaminha extras (`./bin/extend --no-cursor`).
- `extend`: tela virtual 640x480 em `x=-640`, 1:1, cursor via `mss` + blit NSCursor (~8 ms/frame,
  ~35–40 fps de captura, USB 60). Letterbox 1:1 não passa de LANCZOS/unsharp. Log ao vivo a cada 1s
  (`captura N fps / USB N fps / gargalo`).
- Preview do processo USB usa `grab_via_screencapture` — nunca `mss` no pai.
- 179 testes; `make check` = ruff + pytest + prettier; CI verde.
- PRs mergeados #10–#19. Desta sessão: #18 (ponteiro no HDMI via `-D -C`, depois substituído pelo
  blit) e #19 (mss+blit, flags, log, SIGSEGV).

## Tentativas de tamanho de UI (P2016, HDMI 640x480, sem commit)

Alvo: UI “normal” no P2016 sem perder o lock 640x480. Ranking do usuário:

| #         | teste                                                                           | resultado                                    |
| --------- | ------------------------------------------------------------------------------- | -------------------------------------------- |
| A         | virtual **640x480 1x**, HDMI 640x480 1:1                                        | **vencedor** — nítido, UI grande             |
| HiDPI     | virtual **1440x900 points @2x** (backing 2880x1800), BOX/LANCZOS → 640x480      | melhor que B/C, **pior que A** (ainda borra) |
| B         | virtual **1280x960 1x** → 640x480 (2x)                                          | borrão; NEAREST sem unsharp também horrível  |
| C         | virtual 1440x900 1x (não chegou a um trial longo; seria 3x pior que B na borra) | descartado                                   |
| stretch-x | A com `--stretch-x 1.1585` (espreme ~552x480 para o VGA alargar)                | usuário não gostou; captura caiu a ~18 fps   |
| OSD       | Wide / 4:3 / 1:1 no menu do P2016                                               | usuário já testou; não resolve o “aumentado” |

HiDPI ≠ B: B desenha texto 1x e reduz; HiDPI desenha 2x (truque hidpi-mirror/BetterDummy) e reduz.
Ainda perde pro A porque o HDMI continua 640x480.

Pesquisa (USB 2): Fresco + [fl2000drm](https://github.com/ADCDS/fl2000drm) confirmam **640x480 no
USB 2**, 1080p só em USB 3. Compressão RLE do driver oficial não está implementada no userspace.
`screencapture -C` com `-R` é no-op no `CGVirtualDisplay` (byte-idêntico); `-D -C` desenha o
ponteiro mas custa ~134 ms/frame.

## Pendências / próximos passos

1. **Cursor no `extend`**: resolvido. `-C -R` não desenha no virtual; blit NSCursor no `mss`
   (usuário confirmou no HDMI). `--no-cursor` se quiser mais fps.
2. **Tamanho de UI vs nitidez**: resolvido na prática — ficar no A. Não repetir B/C/stretch-x.
3. **HPD/EDID**: sem HPD não dá pra negociar modos. Só resolve trocando o dongle/adaptador.
4. Mirror de tela inteira continua borrado (downscale da retina) — inerente.

## Gotchas operacionais

- Matar por **PID exato**; nunca `pkill -f hagibis_re.py`.
- Matar o processo Python **deixa o helper da tela virtual pendurado** — mate
  `hagibis_virtual_display` também, senão o próximo `extend` falha ao criar a tela.
- `Access denied` / claim fail: desplugar o USB-A do Hagibis e reconectar; `diskutil unmountDisk` o
  fake CD se remontar.
- Diagnóstico sempre com `python -u` (senão um crash engole a saída).
- `REG_ACLK` bit 28 (EOF = ZLP) precisa ficar setado.
