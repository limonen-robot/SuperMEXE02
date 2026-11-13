# NUCLEO-G431KB 全端子対応表

基板の部品面を上から見てUSB端子を上に置いた向き。左側CN4、右側CN3、それぞれ上から1〜15番。図は実寸ではありません。

出典：[ST UM2397 Rev 2、Figure 12 / Table 11](https://www.st.com/resource/en/user_manual/um2397-stm32g4-nucleo32-board-mb1430-stmicroelectronics.pdf)。信号の接続設定は[ST MB1430-G431KBT6-A02公式回路図、6枚目](https://www.st.com/resource/en/schematic_pack/mb1430-g431kbt6-a02_schematic_internal.pdf)も参照。

| 左CN4番号 | Arduino名 | MCU信号 | 右CN3番号 | Arduino名 | MCU信号 |
|---|---|---|---|---|---|
| 4.1 | D1 | PA9 | 3.1 | VIN | VIN |
| 4.2 | D0 | PA10 | 3.2 | GND | GND |
| 4.3 | NRST | NRST | 3.3 | NRST | NRST |
| 4.4 | GND | GND | 3.4 | +5V | +5V |
| 4.5 | D2 | PA12 | 3.5 | A7 | PA2 |
| 4.6 | D3 | PB0 | 3.6 | A6 | PA7 |
| 4.7 | D4 | PB7 | 3.7 | A5 | PA6/PA15 |
| 4.8 | D5 | PA15 | 3.8 | A4 | PA5/PB7 |
| 4.9 | D6 | PB6 | 3.9 | A3 | PA4 |
| 4.10 | D7 | PF0 | 3.10 | A2 | PA3 |
| 4.11 | D8 | PF1 | 3.11 | A1 | PA1 |
| 4.12 | D9 | PA8 | 3.12 | A0 | PA0 |
| 4.13 | D10 | PA11 | 3.13 | AREF | AVDD |
| 4.14 | D11 | PB5 | 3.14 | +3V3 | +3V3 |
| 4.15 | D12 | PB4 | 3.15 | D13 | PB3/PB8 |

## この回路の接続

| 用途 | 物理端子 | Arduino名 | MCU信号 |
|---|---|---|---|
| sensor1 / a | CN3-12 | A0 | PA_0 |
| sensor2 / b | CN3-11 | A1 | PA_1 |
| sensor3 / c | CN3-9 | A3 | PA_4 |
| sensor4 / d | CN3-8 | A4 | PA_5 |
| VCT入力 | CN4-6 | D3 | PB_0 |
| 3V3出力 | CN3-14 | +3V3 | +3V3 |
| 共通GND | CN3-2、CN4-4 | GND | GND |

## 表記と確認範囲

- KiCadのピン識別番号 `3.n` はCN3のn番、`4.n` はCN4のn番です。MCU単体のパッケージ番号とは異なります。
- 全端子とは左右のArduinoヘッダー30本です。USBは給電元と向きを示す説明として表し、USB端子やST-LINK内部の回路・テストパッドは展開していません。
- A4のPA5/PB7、A5のPA6/PA15、D13のPB3/PB8は基板上のソルダーブリッジ設定に関係します。両信号を独立した外部端子として増やしていません。実ボードの設定は未確認です。
- A4にsensor4をつないだ図は、既存のコードで使用するPA_5に対応させたものです。実際のPA_5接続・PB_7との共有設定を断定するものではありません。
- ×印はこの図に外部配線が未記載の端子です。motor側の未説明端子を実機で未使用と断定しません。NRSTやAREFも含めて省略せず表示しています。
- 今回の3V3共通化を表すため、両ボードの3V3端子はpassive属性とし、共通レールをPWR_FLAGで供給宣言しています。5Vは図上未接続です。内部給電経路は未検証です。
- この専用シンボルは公開された公式の端子情報から本プロジェクト用に作成しました。外部ライブラリーのクローンは使用していません。
- PCBフットプリントは今回の対象に含めていません。

## 基板間配線の追加

U1=sensor、U2=motorとして同じ端子配置を使用。VCTリレー、D1→D0、NRST共通スイッチ、3V3/GNDの共有は[基板間配線](INTERCONNECTS.md)を参照。
