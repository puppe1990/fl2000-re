# Handoff — fl2000-re

Resumo do estado do projeto, do que foi medido no hardware e do que falta. Leia junto com
`AGENTS.md` (regras) e `README.md` (uso).

## 2026-10-25 — apagões confirmados no USB, perfil de menor clock em teste

- Log do kernel confirma timeout do endpoint OUT 0x01 com os mesmos bytes parciais do app. Exemplos:
  18:37:35 (917504/1572864), 18:41:17 (833536/1572864), 18:50:15 e 18:50:35.
- PyUSB/libusb1 retorna uma contagem parcial após timeout sem necessariamente lançar exceção. O
  transporte agora verifica o tamanho, não envia EOF de quadro parcial e não esconde erro de ZLP.
- Experimentos com par pixels/EOF em fila e com blocos de 256 KiB também falharam; foram retirados.
  O último bloco do teste falhou às 18:57:01 (1529856/1572864). Não tratá-los como correção.
- As configurações USB U1/U2 já correspondiam ao driver Fresco: REG 0x70 bits 19/20 em 1 e REG 0x78
  bit 17 em 0. O problema não foi atribuído à economia de energia.
- Correção do diagnóstico: REG_STATUS bit 8 = underflow, bit 9 = overflow, conforme
  `src/fl2000_interrupt.h` da Fresco. As etiquetas anteriores estavam invertidas.
- Perfil atual em validação: mesmo raster 1024x768 e desktop 1280x960, PLL 54 MHz (0x001B2105),
  frequência real 49.849 Hz. Reduz ~94 para ~79 MB/s. Não é modo VESA DMT.
- Cada sessão agora registra run ID, commit, hash dos fontes/helper, timing e link USB. Isso permite
  diferenciar uma edição de código de um reinício simples sem depender da memória do usuário.
- Ainda é necessário validar estabilidade por tempo maior e compatibilidade na tela física. Não
  afirmar que a redução de clock eliminou os apagões apenas por uma amostra curta sem falhas.

### Resultado da amostra de 50 Hz

Sessão `21cad34649ef`, fontes `8b10858deb35e5f1`: 300 s de observação, 316 amostras, mediana USB
48,9 fps, zero timeout/quadro parcial/reinício. PLL confirmado em 54 MHz e contador do chip medido a
~49,98 Hz. Maior chamada USB: 151 ms. Essa amostra curta não comprova eliminação dos apagões;
confirmação física e observação mais longa continuam pendentes.

## Objetivo da missão

Deixar a imagem no monitor ligado ao **Hagibis FL2000DX** (`1d5c:2000`) **nítida/legível**. O
`mirror` saiu borrado e o `extend` saiu grande/esticado; investigamos como melhorar.

## Hardware de teste

- MacBook Air M2 (Mac14,15), macOS 14.5.
- Dongle: Hagibis FL2000DX + transmissor IT66121, USB 3.0 (SuperSpeed) na porta direta; 480 Mb/s
  atrás do hub VIA USB 2.
- Monitores: interno 2880x1864 retina; **Dell P2219H** 1920x1080; **Dell P2016** 1440x900.
- Saída do Hagibis testada de duas formas:
  - HDMI → **adaptador HDMI→VGA** → P2016.
  - HDMI → **direto** no P2219H (sem adaptador).

## 2026-10-23 — USB 3: 1440x900 nativo no P2016

O Hagibis passou a enumerar em **SuperSpeed** (`bcdUSB=0x300`, `speed=4`, 5 Gb/s) plugado direto. A
premissa "640x480 é o teto" era do link USB 2, não do chip.

Timings do driver oficial Fresco (`big_table_16bit_r0`, a tabela do bulk 16-bit). O pacote Windows
de 2017 traz o mesmo código (`RENDER_COMPRESSION_AUTOUPDATETIMER_CONTEXT`); a compressão gravity só
é forçada em USB 2, então em USB 3 o caminho é RGB565 cru. Modos novos: `1440x900` (P2016 nativo),
`1920x1080`, `1920x1080@30`; `bars` e `--mode` por comando.

Medido com o P2016 (HDMI→VGA, `--underscan 1.0`):

| teste               | resultado                                                   |
| ------------------- | ----------------------------------------------------------- |
| bars 1440x900       | 146 fps (~379 MB/s) — sobra para 60 Hz                      |
| extend 1440x900     | USB **60,0 fps**, captura ~22 fps, grab 20 ms, sem gargalo  |
| extend 1920x1080@30 | USB 29 fps; P2016 rejeita o timing ("not supported")        |
| bars 1920x1080      | 42 fps (176 MB/s) — sink não aceitou o modo, não conclusivo |

Outros achados:

- **O bug real era o ZLP**: o chip usa o pacote de comprimento zero como EOF do bulk. O repo só
  mandava o ZLP quando o frame era múltiplo de 512 bytes — e 1440x900 (2.592.000) é o único modo não
  múltiplo. Sem ZLP o chip nunca via fim de frame. O driver oficial submete o `zero_length_urb`
  **depois de todo frame**; corrigido em `send_frame` (regressão em
  `test_send_frame_always_terminates_with_zlp`).
- Com o ZLP corrigido as listras **aparecem** em 1440x900, mas **piscam** (1280x800/1280x1024
  também): 60 fps estáveis no chip, frame counter subindo, `lbuf_ovf` normal, sem underflow, monitor
  conectado. Em 640x480 fica estável. O suspeito é o link TMDS/adaptador HDMI→VGA em clock alto (>80
  MHz), não o chip. Teste decisivo: Hagibis direto no HDMI de um sink digital (P2219H).
- **QoS do launchd importa**: com `ProcessType` padrão o pump caía a ~44 fps num frame de 2,6 MB.
  `ProcessType=Interactive` no plist devolveu 60,0. Com 640x480 o frame é 4x menor e o custo fixo de
  escalonamento não aparecia.
- **`--underscan 0.92` reescala**: em modo nativo o `fit_rgb888` deixa de ser identidade e custa
  ~150 ms/frame (captura a 3 fps). Use `--underscan 1.0`.
- `1920x1080@60` pede 249 MB/s; o P2016 não aceita o timing, então não foi validado num sink real.
- **Teto do link**: 1024x768@60 (65 MHz) fica estável; 1280x720 (74 MHz) pisca pouco; 1280x800 (84
  MHz) e 1440x900 (107 MHz) piscam muito. O corte está entre 65 e 74 MHz.
- **OSD do P2016 guarda posição/escala por modo**: depois de trocar o timing a imagem pode ficar
  alguns pixels alta com faixa preta embaixo. O quadro enviado está completo (conferido no
  `preview.png` e no grab do filho), então é o monitor: Display → **Reset Display** devolve o padrão
  daquele modo; `Vertical Position` (50) ajusta fino. `--v-shift` não resolve: o vstart só tem ~2
  linhas de folga no 1024x768 e aumentar o blanking derruba o USB para ~46 fps.
- **`--virtual`**: a tela virtual pode ser maior que o HDMI e é reduzida (1280x960 → 1024x768, UI
  ~20% menor). Tem que ser a **mesma proporção** do modo, senão letterboxa (1280x800 → 1024x768
  deixa barras em cima/embaixo). O downscale pequeno usa LANCZOS direto; o caminho 2x custava 55 ms
  e limitava a captura a ~10 fps (com o fast path fica ~20 fps). O LaunchAgent usa 1280x960.
- **Legibilidade e jitter**: o downscale pequeno leva unsharp 0.7/350/2 (mais nítido que o 1.0/280,
  sem os halos do 0.5/450). O filho de captura roda com `nice(5)` para o pump USB não perder ticks:
  com o grab de ~45 ms o USB voltou a 57–60 fps (antes oscilava 51–58).
- O LaunchAgent sobe `MODE_1024x768` (65 MHz, o mais alto estável) com desktop virtual 1280x960;
  1440x900/1080p via `--mode`.

## Resultado medido (o essencial)

**Só 640x480@60 RGB565 trava.** Todos os modos maiores falharam:

| modo                                 | resultado                                            |
| ------------------------------------ | ---------------------------------------------------- |
| 640x480 RGB565                       | estável, 60 fps                                      |
| 720x480 RGB565 (CEA 480p)            | chiplimpa mas o P2219H rejeita timing de TV          |
| 800x600 RGB565/RGB332                | não trava (precisa 40 MHz; o FL2000 vai até ~27 MHz) |
| 640x400 / 720x450 / 640x360 (custom) | perdem sincronia (o P2219H rejeita modos custom)     |

Outros achados:

- **`buf[:n] = packed` custava 22 ms por frame** (2026-10-11). O array ctypes compartilhado não faz
  slice assign rápido: percorre os 614400 elementos em Python e sozinho limitava o filho de captura
  a ~24 fps. Trocado por `ctypes.memmove` (`fl2000_re/frame_buffer.py`, 0,014 ms). A/B no worker
  real: 17,2 → 84,1 fps. No agent: 23-25 → 68-92 fps, USB 60, `gargalo: nenhum`. O grab+fit+pack do
  caminho real (extend 1:1) custa só ~7,7 ms; quem mandava era a cópia.
- **O filho agora é ritmado pelo refresh do modo** (`capture_period_s`, `CAPTURE_LEAD = 1.15`,
  2026-10-11). Livre, ele passou a produzir ~123 fps para um pump de 60 Hz: 43,3% de um core contra
  23,7% ritmado, medidos back-to-back no mesmo processo. Quando o grab estoura o período o filho
  simplesmente não dorme — acompanha o que a máquina dá, sem atraso artificial (no agent oscila
  61-69 fps conforme o WindowServer). O pump nunca pula tick, então repetir o frame é esperado.
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
- `extend`: tela virtual 640x480 em `x=-640`, 1:1, cursor via `mss` + blit de uma seta desenhada
  in-process (~8 ms/frame). USB continua 60 Hz (reenvia o último frame). Captura default
  `EXTEND_CAPTURE_FPS = 20` (`--capture-fps`) — 69 fps via `CGWindowListCreateImage` pinava o
  WindowServer no Air de 8 GB (2026-10-20: grab 636–2735 ms, quatro `btn_rst`). Ponteiro **na tela
  Hagibis** sobe a `FLUID_CAPTURE_FPS = 60` com `lead=1` (1/60 s; o lead 1.15 dava ~69/s). Nas
  outras telas fica 20. Ponteiro parado 10 s cai a `IDLE_CAPTURE_FPS = 5` (USB ainda 60); o primeiro
  movimento na Hagibis volta a 60, nas outras a 20. Grab ≥80 ms dispara `capture_backoff_s` (cap 2
  s) para não martelar o compositor. O `NSCursor` (AppKit) pendura sob launchd, então o sprite do
  ponteiro é gerado localmente (`cursor_overlay.builtin_arrow_sprite`). Letterbox 1:1 não passa de
  LANCZOS/unsharp. Log ao vivo a cada 1s (`captura N fps / USB N fps / gargalo`); o LaunchAgent
  rotaciona o log se passar de 2 MB (o arquivo chegou a 260 MB, 1 linha/s desde 20/09).
- Preview do processo USB usa `grab_via_screencapture` — nunca `mss` no pai.
- Log com timestamp local (`log_time.py`, instalado no `cli.main`): sem ele não dava pra situar no
  tempo o incidente de 2026-09-20. `CaptureProcess` respawna o filho de captura quando ele morre
  (SIGKILL) ou trava (sem frames por 5s), mantendo o último frame no HDMI; esgotadas as 5 tentativas
  o pai sai (`CaptureDeadError`) pro LaunchAgent religar. Antes, um filho morto deixava o HDMI
  congelado pra sempre e gerava ~15 mil linhas de `captura 0.0 fps`.
- 254 testes; `make check` = ruff + pytest + prettier; CI verde.
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

1. **Cursor no `extend`**: resolvido, inclusive no LaunchAgent. `-C -R` não desenha no virtual; o
   blit usa uma seta desenhada in-process (`cursor_overlay.builtin_arrow_sprite`) porque
   `NSCursor`/AppKit pendura sob launchd. `--no-cursor` se quiser mais fps.
2. **Tamanho de UI vs nitidez**: resolvido na prática — ficar no A. Não repetir B/C/stretch-x.
3. **HPD/EDID**: sem HPD não dá pra negociar modos. Só resolve trocando o dongle/adaptador.
4. Mirror de tela inteira continua borrado (downscale da retina) — inerente.
5. **Freeze 2026-10-20**: captura 20 no resto, 60 fluido só com o ponteiro na Hagibis (`lead=1`), 5
   fps após 10 s parado, backoff no grab lento. Recarregar o LaunchAgent para valer
   (`launchctl kickstart -k gui/$UID/com.fl2000-re.extend`). Sem commit até o usuário pedir.

## Gotchas operacionais

- Matar por **PID exato**; nunca `pkill -f hagibis_re.py`.
- Matar o processo Python **deixa o helper da tela virtual pendurado** — mate
  `hagibis_virtual_display` também, senão o próximo `extend` falha ao criar a tela.
- `Access denied` / claim fail: desplugar o USB-A do Hagibis e reconectar; `diskutil unmountDisk` o
  fake CD se remontar.
- Diagnóstico sempre com `python -u` (senão um crash engole a saída).
- `REG_ACLK` bit 28 (EOF = ZLP) precisa ficar setado.
