# SuperMEXE02

NUCLEO-G431KBを2台使用し、直動アクチュエータの動作生成とセンサー値に基づくPID制御を行う、拍動循環シミュレータ向けの制御システム。

## ドキュメント

- **[KiCad回路図](hardware/README.md)**：両G431KBと4入力、AZD-K×4、リレー×5、24 V電源×2の3ページ構成。


- **[システム・ハードウェア仕様書](docs/SYSTEM_SPEC.md)**：機器構成、電源・配線、全ピン割当、計測・制御・通信仕様、停止条件、開発環境、未確認事項。
- **[アプリの操作説明](motor_app/README.md)**：設定、ビルド・書込み、通常運転、フィードバック、保存・読込み。
- **[KiCad 10・MCP連携の設定](docs/KICAD_MCP_SETUP.md)**：導入済み環境、AIからの回路図操作、再設定・診断手順。
- [α→βの変換表CSV](AlphaToBeta_conversion/)：書式例と既存の変換表。

## 現在の実装

| 項目 | 内容 |
|---|---|
| motor | MOTOR_V6、最大4軸のCW/CCW・ZHOME、VCT、最大3系統のPID |
| sensor | SENSOR_V1、4系列100 Hz取得、VCT後の時刻指定取得 |
| PC | Windows、Python/Tkinter、各ボードへUSBシリアル115200 bps |
| 動作 | 軸ごとの移動・待機・ZHOME、同時実行、繰り返し、手動操作 |
| フィードバック | 数式→CSV→目標値、初期位置移動、指令積算位置表示 |
| 保存 | 通常運転・センサー・PID・変換表をJSONに保存 |

PCと各ボードは別々のUSB COMで接続し、motor PA_0でVCTリレーを制御し、接点経由でsensor PB_0へ同期信号を入れる。両ボードのGNDは共通。
詳細な電源・リレー回路と、未確認の配線事項は仕様書を参照する。

## リポジトリ構成

```text
G431KB_motor/             motorファームウェア、PID
G431KB_sensor/            sensorファームウェア
motor_app/                PCアプリ、操作説明、自動テスト
AlphaToBeta_conversion/   変換表CSV
hardware/                 KiCad 10回路図、専用シンボル、プレビュー
docs/SYSTEM_SPEC.md       システム・ハードウェア仕様書
.vscode/                  ビルド・Flash・アプリ起動タスク
```

## 開発・起動

Mbed Studio同梱のPython・Mbed CLI 1・Arm Compiler 6を利用する。各プロジェクトの`mbed-os.lib`がMbed OSの版を指定しており、本体とBUILDはGitに含めない。新規取得時は各Mbedプロジェクトで`mbed deploy`等により依存を復元する。

VS Codeでこのリポジトリのルートを開き、タスクから次を実行する。

1. `Mbed: Build both`：両MCUのビルド。
2. 各ボードの`Mbed: G431KB_motor Flash` / `Mbed: G431KB_sensor Flash`：対応するUSBドライブを指定して書込み。
3. `App: Motor control`：アプリ起動後、motorとsensorの異なるCOMを選択。

COM番号とUSBドライブ文字は固定ではない。現在のアプリにはMOTOR_V6とSENSOR_V1を使用する。

2026-11-11時点でアプリの自動テスト89件、両MCUのビルドを確認済み。研究条件での性能評価・校正状況と今後の拡張範囲は[仕様書](docs/SYSTEM_SPEC.md)に記載する。
