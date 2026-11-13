# KiCad 10とAIのMCP連携

2026-11-13、WindowsのKiCad 10.0.8に`lucatib/kicad-mcp`を導入し、CodexのMCPサーバー`kicad`へ登録した。Mbed StudioのPythonとは別の、KiCad専用の仮想環境を使用する。

## 1. 導入したもの

| 項目 | 内容 |
|---|---|
| KiCad | 10.0.8、`C:\Program Files\KiCad\10.0` |
| Python | KiCad同梱3.11.5から仮想環境を作成 |
| MCPサーバー | [lucatib/kicad-mcp](https://github.com/lucatib/kicad-mcp)、0.2.0 |
| 確認したソース | `6e6666dd1f11bd66852eacfd1f07f9bae97767f8` |
| 取得先 | `%USERPROFILE%\Documents\Codex\tools\kicad-mcp` |
| 仮想環境 | 上記の`.venv` |
| 直接依存の確認版 | `mcp==2.3.0`、`kicad-python==0.8.0`、`olefile==0.47` |
| Codexの登録名 | `kicad` |

第三者MCPのソース本体や仮想環境は、このSuperMEXE02リポジトリへコピーしていない。各PCで別途取得する。個人用のCodex設定ファイルもGitには含めない。

## 2. 今回のPCで使い始める

1. Codexを再起動して、追加したMCP設定を読み込む。
2. 回路図の読み取り・生成・編集は、KiCadを閉じた状態でも利用できる。
3. ライブで基板を操作するときは、KiCad 10のプロジェクトマネージャーからPCBエディターを開く。
4. 例として「KiCadの接続状態を確認して」「標準ライブラリからNUCLEOのシンボルを探して」「この回路図の接続を調べて」と依頼する。

Codex CLIとCodexのVS Code拡張はMCP設定を共有する。Codex拡張も必要に応じて再読み込みする。これはVS Codeの通常のテキスト編集や、別製品のCopilotへ自動的に同じ設定を追加するものではない。

現在の構成では、VS Code内の回路図ビューアー拡張は導入していない。視覚的な確認はKiCad本体、またはMCPから出力したSVG/PDFを使用できる。

## 3. KiCad 10での回路図と基板の違い

| 対象 | 操作方法 | KiCadを開く必要 |
|---|---|---|
| 回路図 | `.kicad_sch`のファイル編集、`kicad-cli`による検査・出力 | なし |
| 基板ファイル | ファイルベースの読取り等 | ツールによる |
| 開いている基板のライブ操作 | KiCad IPC API | PCBエディターが必要 |

KiCad 10には回路図のライブIPC APIがない。MCPが回路図ファイルを変更しても、開いている回路図エディターへ自動反映されない。

手作業で編集した場合は、MCPへ編集を依頼する前にKiCadで保存する。MCPがファイルを変更した後は、KiCadの「ファイル→元に戻す／Revert」、または閉じて開き直す操作で再読込みする。再読込み前にGUIから保存すると、MCP側の変更を古い表示内容で上書きすることがある。

今回のPCではIPC APIサーバーを有効化し、KiCad 9のPythonを指していた設定を`C:\Program Files\KiCad\10.0\bin\python.exe`へ修正した。KiCad 9もインストールされているため、MCPの環境変数で10系を明示的に指定する。

## 4. 別PCで同じ構成を作る

KiCad 10、Git、Codexを導入したWindows PCで実行する。下記の取得先がすでに存在する場合は、新規cloneを重ねず、既存の内容と版を確認する。

```powershell
$kicadRoot = 'C:\Program Files\KiCad\10.0'
$mcpRoot = Join-Path $env:USERPROFILE 'Documents\Codex\tools\kicad-mcp'

git clone https://github.com/lucatib/kicad-mcp.git $mcpRoot
git -C $mcpRoot checkout 6e6666dd1f11bd66852eacfd1f07f9bae97767f8

& "$kicadRoot\bin\python.exe" -m venv --system-site-packages "$mcpRoot\.venv"
& "$mcpRoot\.venv\Scripts\python.exe" -m pip install 'mcp==2.3.0' 'kicad-python==0.8.0' 'olefile==0.47'
& "$mcpRoot\.venv\Scripts\python.exe" -m pip install -e $mcpRoot
```

KiCadを別の場所へ導入した場合は`$kicadRoot`を変更する。KiCadの設定でIPC APIサーバーを有効化し、プラグイン用Pythonも10系を選ぶ。

Codexへ登録する。

```powershell
codex mcp add kicad `
  --env "KICAD_MCP_KICAD_ROOT=$kicadRoot" `
  --env 'PYTHONUTF8=1' `
  -- "$mcpRoot\.venv\Scripts\python.exe" -m kicad_mcp

codex mcp get kicad
```

`PYTHONUTF8=1`は今回確認した日本語Windows環境で必要。未指定では、KiCad CLIのUTF-8出力をPythonがcp932で読み取って失敗し、回路図生成・検査が途中停止した。

登録後はCodexを再起動する。起動中の会話へツール一覧が即時追加されることは確認していない。

## 5. 診断

```powershell
$env:KICAD_MCP_KICAD_ROOT = $kicadRoot
$env:PYTHONUTF8 = '1'
& "$mcpRoot\.venv\Scripts\python.exe" -m kicad_mcp doctor
```

確認する項目：KiCad 10の検出、`pcbnew`、`kipy`、MCP SDK、IPC有効化、シンボルライブラリ。
KiCadを閉じている場合はLive connectionが未接続と表示されるが、ファイルベースの回路図操作は利用できる。基板ライブ操作では、KiCadのプロジェクトマネージャーとPCBエディターを開いてから診断する。

## 6. 実施した確認

MCPのstdio接続を実際に起動し、次を確認した。

- 初期化とツール一覧取得：51ツール。
- KiCad 10.0.8と、標準シンボルライブラリ224種の検出。
- `Connector_Generic:Conn_01x02`の検索。
- 作業用のテスト回路図生成と、2個目のコネクターの追加編集。
- KiCad公式CLIによるERC実行。
- KiCad公式CLIによるSVG出力。

テスト回路図は接続機能を確認するための作業用データであり、SuperMEXE02の実配線を表すものではない。実際の基板を開いたライブ編集は未確認。

## 7. SuperMEXE02の回路図作成への適用

本システムの信号割当は[システム仕様書](SYSTEM_SPEC.md)に記録してある。今後、確定した配線を`hardware/`等へKiCadプロジェクトとして保存し、コードと一緒にGit管理できる。

回路図を実配線に対応させるには、共通VCCの接続端子、AZD-K入力のインターフェース、リレー型番と配線、センサー型番・電源、C/DのZHOME分離を確定する必要がある。今回のMCP導入では、それらの未確認事項を推測して実機用回路図を作成していない。

## 参考

- [導入したMCPサーバーのREADME](https://github.com/lucatib/kicad-mcp)
- [OpenAI公式：CodexのMCP設定](https://developers.openai.com/codex/mcp)
- [KiCad公式CLIドキュメント](https://docs.kicad.org/10.0/en/cli/cli.html)
