<div align="center">

# RPG Translator

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
[![Release](https://img.shields.io/github/v/release/3Asterism/RPGTranslator?include_prereleases&label=release&color=success)](../../releases)

🌐 [English](README.md) · [简体中文](README.zh-CN.md) · 日本語

[🚀 クイックスタート](#quick-start) · [📥 Releases](../../releases) · [⚙️ 設定方法](#configure-engine) · [🐛 Issue を報告](../../issues)

**RPG Maker / WOLF RPG エディター ゲームテキスト抽出 → AI 翻訳 → 書き戻しツール。**

ゲームフォルダをウィンドウにドラッグするだけで、テキスト抽出・LLM 翻訳・ゲームプロジェクト
本体への書き戻しまでワンストップで完了します。書き戻し前に原文版を自動バックアップするので、
「原文表示 / 訳文表示」でいつでも日中対照ができ、翻訳版フォルダを別に用意する必要もありません。

MTool に近い使用感ですが、翻訳メモリはより細かく管理しています。同一の原文はデフォルトで
同じ訳語を再利用しつつ、QA 段階では「同じ文でも文脈によって訳し分けが必要なケース」を
別途抽出します。全置換で押し切るような雑な処理はしません。

<img src="docs/screenshots/main-window.png" alt="RPG Translator メイン画面" width="640">

</div>

<br>

<details>
<summary><b>📋 目次</b></summary>
<br>

- [✨ 主な特徴](#main-features)
- [🎮 対応エンジン](#supported-engines)
- [🚀 クイックスタート](#quick-start)
- [⚙️ 翻訳エンジンの設定：オンライン API / ローカルモデル](#configure-engine)
- [🧑‍💻 開発者向け：CLI / テスト / パッケージング](#dev-guide)
- [⚠️ 既知の制限事項](#known-limitations)
- [🛠 技術スタック](#tech-stack)
- [📄 ライセンス](#license)

</details>

---

<a id="main-features"></a>
## ✨ 主な特徴

### 翻訳品質と一貫性
- 🔒 **制御コード保護**：`\C[n]` `\N[n]` `\V[n]` などの変数/カラーコードは翻訳前にプレース
  ホルダへエスケープし、翻訳後に正確に復元・整合性チェックします。`\n<キャラクター名>本文`
  という話者タグ表記（一部プロジェクトの慣習）は、キャラクター名と本文を分離してそれぞれ
  翻訳してから結合するため、モデルは山括弧を一切見ません。「このマークアップを残すべきか」
  という誤判断を根本から防ぎます。
- ♻️ **翻訳メモリによる重複排除**：同一原文は API を一度しか呼ばず、効率と一貫性を両立します。
- 🔍 **QA 整合性スキャン**：同一原文でも文脈によって訳し分けが必要になり得るケースを、レビュー
  用リストとして別途書き出します（全置換ではありません）。

### 安定性と効率
- ⏯️ **中断・再開対応**：作業途中で手動停止したりプロセスが強制終了されても、再起動すれば続きから
  再開でき、翻訳済みの内容が再翻訳されることはありません。
- 🔁 **失敗時の自動リトライ**：一部の翻訳失敗が全体を巻き込むことはなく、失敗した項目は未翻訳
  状態のまま保持されます。1 回の翻訳が終わると自動的にその場で 2 回リトライし（5 秒間隔）、
  それでも失敗する項目は「失敗項目を再試行」ボタンで再実行できます（抽出からやり直す必要は
  ありません）。
- 🔀 **マルチプロバイダーフェイルオーバー**：メインプロバイダーでエラー（レート制限・5xx）が
  連続すると自動的にバックアッププロバイダーへ切り替え、指数バックオフでリトライします。
- 🧯 **レート制限への適応バックオフ**：429 に当たると、同一プロバイダーへの全並行リクエストが
  1 つのクールダウンウィンドウを共有します（`Retry-After` があればそれを優先、なければ連続
  ヒット回数に応じて指数バックオフ）。個別リトライが同じレート制限ウィンドウに繰り返し衝突
  するのを防ぎます。
- ⚡ **同時実行数の制限 + バッチリクエスト**：時間と（DeepSeek の prompt caching と組み合わせて）
  トークンの両方を節約します。バッチサイズは設定パネルで調整可能です。

### ワークフロー
- 🔄 **原文/訳文のワンクリック切り替え**：翻訳に問題があれば、注入をやり直さずに原文へ戻して
  確認できます。
- 📦 **翻訳パッケージの共有**：軽量な `.rpgtrans.json` としてエクスポートすれば、同じバージョンの
  ゲームを持つ他のユーザーがそのままインポートして再利用でき、API 利用枠を消費せずに済み
  ます。MTool 形式（`ManualTransFile.json`）でのエクスポートにも対応しています。
- 📂 **単一 exe の自動アンパック**：ドロップしたゲームが Enigma Virtual Box で固められた単一 exe
  （`www/data` がディスク上に見当たらない）の場合、自動的にアンパックしてから再判定します。
  手動でアンパックツールを探す必要はありません。

---

<a id="supported-engines"></a>
## 🎮 対応エンジン

| エンジン | 状態 | 備考 |
|---|:---:|---|
| RPG Maker MV / MZ | ✅ | プレーンな JSON。実プロジェクトでイベントコマンドのエンコード表を較正済み。 |
| RPG Maker VX Ace | ✅ | Ruby Marshal バイナリ形式。メッセージウィンドウのピクセル単位動的改行ランタイムパッチを含む（spec 9.2.b、詳細は下記[既知の制限事項](#known-limitations)）。データベース/イベントテキストの抽出は実プロジェクトで検証済み。 |
| RPG Maker XP | ✅ | 実際の XP プロジェクト（GitHub 上の GPL-3.0 二次創作ゲーム torresflo/Pokemon-Obsidian）で検証し、実機でしか顕在化しない 2 件のバグを修正済み（詳細は下記[既知の制限事項](#known-limitations)）。 |
| RPG Maker VX | ✅ | XP と同じアダプターコードを共有。実際の VX プロジェクト（GitHub 上のオープンソース二次創作ゲーム ambratolm-games/flower-in-pain）で検証し、Ruby Marshal 書き込みライブラリのオブジェクト参照バグを 1 件修正済み（詳細は下記[既知の制限事項](#known-limitations)）。 |
| WOLF RPG エディター（ウディタ） | ✅ | WOLF RPG Editor 公式同梱のサンプルプロジェクトで検証済み（Map/CommonEvent/Database の 3 種類のファイルを全カバー、現行エディタバージョンのデフォルトである LZ4 圧縮形式にも対応）。WolfPro による暗号化保護、および従来の XOR 暗号化を施したプロジェクトには非対応。 |
| RPG Maker 2000/2003 | ❌ | まったく異なる形式のため、明確に対象外としています。 |

> **ドロップしたのが単一 exe で、プロジェクトファイルが散らばっていない場合は？** 多くの
> RPG Maker MV/MZ ゲームは [Enigma Virtual Box](https://enigmaprotector.com/en/aboutvb.html)
> を使い、`www` リソースフォルダと nw.js ランタイムをまるごと 1 つの exe にまとめて配布して
> います（ディスク上に `www/data` は見当たらず、数百 MB～数 GB の exe が 1 つあるだけ）。この
> ようなフォルダをドロップして通常の判定に失敗し、かつトップレベルにこの形式で固められた exe
> が見つかった場合、自動的に同階層の `<元のフォルダ名>_unpacked`（アンパック済み）ディレクトリ
> へ展開し（サイズが大きいと少し時間がかかります）、展開後は自動的にエンジンを再判定します。
> MV/MZ か VX Ace/XP/VX/WOLF かを問わず、このアンパック処理はエンジンの種類に依存しません。

---

<a id="quick-start"></a>
## 🚀 クイックスタート

ビルド済みの Windows 版は [Releases](../../releases) にあります。Python のインストールは
不要です。ソースから実行する場合：

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

`.env.example` を `.env` にコピーして API キーを設定します（または GUI の設定パネルに直接
入力する方法もあり、その場合はシステムの資格情報マネージャー経由で保存され、平文ファイルには
残りません）：

```
DEEPSEEK_API_KEY=あなたのキー
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

GUI を起動：

```bash
.venv\Scripts\rpg-translator-gui.exe
```

### 使い方

1. **ゲームフォルダ（または `Game.exe`）をドラッグ＆ドロップ** → エンジンを自動判定
2. 「翻訳開始」をクリック → 翻訳はバックグラウンドで実行され、いつでも「停止」できます。
   失敗した項目は自動で数回リトライされ、それでも失敗したものは「失敗項目を再試行」で
   再実行できます
3. 「ゲームに注入」をクリック → ゲームプロジェクトへその場で書き戻します（注入前に原文版を
   自動バックアップ）
4. 「原文表示 / 訳文表示」で日中対照ができます。また「翻訳パッケージをエクスポート」で
   同じゲームをプレイする他の人と共有できます

API キー、同時実行数、バッチサイズなどの設定はウィンドウ右上の「⚙ 設定」ボタンから行います。

---

<a id="configure-engine"></a>
## ⚙️ 翻訳エンジンの設定：オンライン API / ローカルモデル

<p align="center">
  <img src="docs/screenshots/settings-dialog.png" alt="設定パネル" width="420">
</p>

設定パネル（右上の「⚙ 設定」）の最初の項目「翻訳エンジン」で両者を切り替えられます。選択した
方が使われ、互いに干渉せず、いつでも切り替え可能です。それぞれの設定は個別に保存されます
（オンラインは `.env`/システム資格情報マネージャー、ローカルモデルも同様）。

### オンライン（クラウド API、デフォルト）

専用 GPU がない場合や、ローカルマシンのリソースを使いたくない場合に向いています。デフォルトは
DeepSeek ですが、OpenAI 互換の `/v1/chat/completions` プロトコルを実装しているプロバイダー
（Alibaba Cloud Bailian、SiliconFlow など）であれば何でも利用できます。

設定パネルの「オンラインプロバイダー」で入力する項目：
- **API キー**：システムの資格情報マネージャー（Windows 資格情報マネージャー / keyring）経由で
  保存され、平文ファイルには残りません
- **ベース URL**：空欄にするとデフォルトの `https://api.deepseek.com` が使われます。他の互換
  プロバイダーを使う場合はそのアドレスを入力してください
- **モデル**：ドロップダウンから選択するか、直接入力できます（例えば安価/高性能なプランへの
  切り替えなど）

GUI を使わず、プロジェクトルートの `.env` に直接設定することもできます（`.env.example` を
コピー）：

```
DEEPSEEK_API_KEY=あなたのキー
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

「バックアッププロバイダー」（任意）：メインプロバイダーでエラー（レート制限・5xx）が連続
すると自動的に切り替わります。3 つのフィールドをすべて空欄にすると無効化され、従来どおりの
動作になります。

<details>
<summary><b>ローカルモデル（例：Ollama で動かす Sakura）— クリックしてデプロイ手順を表示</b></summary>

専用 GPU がある（実測では 12GB VRAM で 7B 量子化モデルが動作）、完全オフラインで翻訳したい、
あるいは翻訳に API 費用を払いたくない場合に向いています。[SakuraLLM/GalTransl](https://github.com/SakuraLLM/SakuraLLM)
系列のモデル専用に調整したプロンプトテンプレート（`translate/sakura_prompt.py` 参照）を使用
しており、オンライン用のプロンプトをそのままローカルモデルに流用しているわけではありません。
この種の小型モデルは固定テンプレートでファインチューニングされているため、別の形式を与えると
精度が落ちます。

デプロイ手順（Ollama を例にしていますが、Windows/Linux 共通です）：

1. [Ollama](https://ollama.com/download) をインストール
2. GalTransl 系列の GGUF 重みをダウンロード。例：
   [SakuraLLM/Sakura-GalTransl-7B-v3.7](https://huggingface.co/SakuraLLM/Sakura-GalTransl-7B-v3.7)
   （12GB VRAM なら Q5_K_S/Q6_K 量子化を推奨。VRAM が少ない場合は IQ4_XS を使用）
3. `Modelfile` を作成：
   ```
   FROM /path/to/sakura-galtransl-7b-v3.7-q5_k_s.gguf
   PARAMETER temperature 0.3
   PARAMETER top_p 0.8
   PARAMETER num_ctx 4096
   ```
4. `ollama create sakura-galtransl -f Modelfile` を実行し、続けて `ollama serve`（デフォルトで
   `127.0.0.1:11434` で待ち受け。同じ LAN 内の別マシンからアクセスする場合は、`ollama serve`
   を起動する前に環境変数 `OLLAMA_HOST=0.0.0.0:11434` を設定してください）

設定パネルで「翻訳エンジン」を「ローカルモデル」に切り替え、「ローカルプロバイダー」に
入力します：
- **ベース URL**：例 `http://127.0.0.1:11434/v1`（同じ LAN の別マシンならその LAN 内 IP を
  入力）
- **モデル名**：`ollama create` で付けた名前。例：`sakura-galtransl`
- **API キー**：通常は空欄で構いません。Ollama はデフォルトでこのフィールドを検証しません

既知の制限事項：ローカルの小型モデルはバッチ翻訳時にまれに行数がずれることがあります（自動的
に 1 行ずつのリトライへフォールバックするため訳文が失われることはありませんが、遅くなります）。
人名などの固有名詞の音訳一貫性はオンラインの大型モデルほど安定していません（プロジェクト
レベルの用語集による制約がないため）。

</details>

<details>
<summary><b>完全版：ローカルモデル同梱、セットアップ不要 — クリックして展開</b></summary>

自分で Ollama をインストールしたりモデルをダウンロードしたくない場合、[Releases](../../releases)
にある「完全版」（`RPGTranslator-full-*`、分割圧縮アーカイブ、NVIDIA GPU が必要）には CUDA 版
llama.cpp エンジンと [SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF](https://huggingface.co/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF)
（q6k 量子化）モデルファイルがあらかじめ同梱されています。設定パネルを「ローカルモデル」に
切り替え、ベース URL/モデル名を空欄のまま「翻訳開始」をクリックすると、同梱エンジンが自動的に
起動します（初回のモデル読み込みは VRAM への転送に数十秒かかります）。手動設定は不要です。
別の場所にデプロイしたサービスを使いたい場合はベース URL を入力すれば、そちらが優先され
同梱エンジンには奪われません。

同梱モデルファイルは [CC-BY-NC-SA-4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
ライセンス（表示 - 非営利 - 継承）のもとで [SakuraLLM](https://github.com/SakuraLLM) により
学習・公開されたものです。本プロジェクト自体も無料・非商用で配布しています。

</details>

---

<a id="dev-guide"></a>
## 🧑‍💻 開発者向け：CLI / テスト / パッケージング

<details>
<summary>クリックして展開（コントリビューター向け。一般ユーザーは GUI を使えば十分です）</summary>

### CLI（開発・デバッグ用。一般ユーザー向けの主要な入り口ではありません）

```bash
rpg-translator extract   <プロジェクトディレクトリ> --out units.db
rpg-translator translate --db units.db --concurrency 8 --batch-size 50
rpg-translator qa        --db units.db --export conflicts.csv
rpg-translator inject    --db units.db --project <プロジェクトディレクトリ> --out <出力ディレクトリ>
rpg-translator run       <プロジェクトディレクトリ> --out <出力ディレクトリ>
```

### テスト

```bash
.venv\Scripts\pytest
```

一部のテストは実際に設定済みの LLM API を呼び出します。ローカルに `DEEPSEEK_API_KEY` が
設定されていない場合は自動的にスキップされ、失敗にはなりません。

### パッケージング

```bash
.venv\Scripts\python scripts\build.py
```

`dist/RPGTranslator/`（PyInstaller の `--onedir` モード）を生成します。現時点では開発マシン
での起動確認のみで、Python 未インストールのクリーンな Windows 環境ではまだ検証していません。
配布前に各自で確認することをおすすめします。

#### 完全版（CUDA エンジン + モデル同梱）

```bash
.venv\Scripts\python scripts\build_full.py
```

上記の軽量版に加えて、llama.cpp 公式のビルド済み CUDA バイナリと Sakura の GGUF モデル
ファイルを追加でダウンロードし（合計 10GB 以上、初回はネットワーク状況によって時間が
かかります）、`dist/RPGTranslator/resources/local_engine/` に組み込んだ上で、7z の分割
アーカイブとして `dist/RPGTranslator-full-v<version>.7z.001`、`.002`……を生成します（各
ボリュームは 1900MB 未満に抑え、GitHub Release の単一ファイル 2GB 上限を回避）。CI/自動
テストでは実行されず、リリース前に手動で実行する作業です。`scripts/build_full.py` 先頭の
バージョン/チェックサム定数は、新しいビルドが正常に動作することを人間が確認してから更新
してください。

ダウンロードはレジューム・失敗時リトライ・キャッシュヒット時のスキップに対応しています。
`--work-dir`（デフォルトは `dist/_build_full_cache`）配下にすでにダウンロード済みのファイル
があれば、`--force-redownload` を付けない限り再ダウンロードしません。中国本土からの
GitHub Release/HuggingFace へのアクセスは不安定なことが多いため、以下と組み合わせて使えます：
`HTTPS_PROXY`/`HTTP_PROXY`（httpx がデフォルトで読み込むため、システムプロキシを設定して
いればコード変更は不要）、`LLAMA_CPP_RELEASE_BASE_URL`（自前のリバースプロキシ/ミラーの
プレフィックスに置き換え）、`HF_ENDPOINT`（HuggingFace のドメインを置き換え、例えば
`https://hf-mirror.com`）。

</details>

---

<a id="known-limitations"></a>
## ⚠️ 既知の制限事項

<details>
<summary>クリックして展開（エンジン実装の詳細。一般ユーザーは読み飛ばして構いません）</summary>

- **RGSS 系エンジン（VX Ace/XP/VX）が共有する Ruby Marshal 書き込みライブラリの、実機でしか
  顕在化しないオブジェクト参照バグ（修正済み）**：実際の XP/VX プロジェクト（それぞれ GitHub
  上のオープンソース二次創作ゲーム torresflo/Pokemon-Obsidian、ambratolm-games/flower-in-pain）
  で検証した結果判明。サードパーティ製 `rubymarshal` ライブラリの `Writer.must_write` は、
  「このオブジェクトは書き込み済みで逆参照を書くべきか」を Python の `id(obj)` のみで判定して
  おり、`str`/`bytes` のトップレベル文字列値を正しく登録していない（`RubyString` のみ登録
  される）上、CPython のメモリアドレス再利用にも対応していません。この 2 つの問題が重なった
  結果、実際のマップファイル 8 個中 3 個で、書き戻し後に自分自身すら正しく読み込めなくなる
  （あるいはより厄介なことに、エラーは出さずに別のオブジェクトとして静かに読み込まれ、データ
  が壊れる）事象が発生しました。`rvdata2_codec.py` に `_SafeWriter` サブクラスのラッパーを
  追加して対処済みで、実プロジェクトで再検証済みです。詳細は同ファイル冒頭のコメントと
  `tests/test_rvdata2_codec.py` の回帰テストを参照してください。
- **XP 固有の文字列エンコーディングバグ（修正済み）**：XP（およびおそらく VX）が使う古い
  バージョンの Ruby（1.8、文字列にエンコーディング情報を持たない）で marshal された文字列は、
  VX Ace（Ruby 1.9 以降）のように `rubymarshal` が自動的にデコードしてくれず、生の `bytes`
  のまま渡ってきます。旧コードはこれに対して Python の `str()` を直接呼んでいたため、抽出
  された「テキスト」は実際には `b'...'` という repr リテラルで、まったく使い物にならず、
  書き戻し時も正しく bytes へ再エンコードされていませんでした。`_rgss_common.py` に
  `rv_str`/`_encode_like`（まず UTF-8 を試し、失敗したら cp932 にフォールバック）を追加して
  修正済みで、実際の XP/VX プロジェクトで再検証済みです。
- VX Ace のメッセージウィンドウ用ピクセル単位動的改行ランタイムパッチ（spec 9.2.b）は実装済み
  で、実プロジェクトへの注入検証も完了しています。`Scripts.rvdata2` に `Window_Message#process_character`
  への monkey patch を追記し、`contents.text_size` で計測した実際のピクセル幅から改行位置を
  決定、4 行を超える場合はエンジン標準のページ送りロジックをそのまま再利用します。既知の
  サードパーティ製メッセージシステムスクリプト（YEA/Galv/Luna/MOG など、キーワードで検出）が
  見つかった場合は自動的にスキップし、推定ベースの再配置にフォールバックします。実際の VX Ace
  プロジェクトで、パッチが正しく注入されること、既存の 100 以上のスクリプトエントリが 1
  バイトも変わらないこと、パッチ適用後のゲームがエラーなく起動することは検証済みです。ただし
  現在の開発環境では DirectX 描画のスクリーンショットが撮れないため、実際の改行・ページ送りの
  見た目はまだ目視確認できていません。スクリーンショットが撮れる環境で改めて確認することを
  推奨します。
- WOLF 形式には公式ドキュメントがありません。`wolf_binary.py` は WOLF RPG Editor 公式同梱の
  サンプルプロジェクトで検証済みで、Map/CommonEvent/Database の 3 種類のファイル（現行エディタ
  バージョンのデフォルトである LZ4 圧縮形式、および v3.5 での Page/Command 構造の変更を含む）
  をカバーしています。WolfPro による暗号化保護、および従来の XOR 暗号化を施したプロジェクト
  には引き続き非対応で、遭遇した場合は推測や無言での文字化けではなく、明示的にエラーを返し
  ます。
- PyInstaller で生成した exe がアンチウイルスソフトに誤検知されることがあります。これは
  一般的によく知られた現象です。`scripts/build.py` にはすでに `--noupx`（UPX 圧縮ラッパーは
  誤検知の主な原因の 1 つ）を追加してリスクを減らしていますが、コード署名証明書がないため
  完全になくすことはできません。
- 単一 exe の自動アンパックは現状 Enigma Virtual Box（`evbunpack`）による形式のみに対応して
  います。VMProtect/Themida のようなプロテクターや、リソースを NSIS インストーラーに詰め
  込んだ配布形式はカバー対象外で、その場合は通常どおり「対応エンジンが検出されませんでした」
  という結果にフォールバックします。

WOLF 形式のリバースエンジニアリングは、[wolftrans](https://github.com/elizagamedev/wolftrans)、
[WolfTL](https://github.com/Sinflower/WolfTL)、
[rewolf-trans](https://github.com/KCFindstr/rewolf-trans) という 3 つのコミュニティプロジェクト
による調査成果を相互検証のうえ移植したものです（詳細は `engines/wolf_binary.py` 冒頭のコメント
を参照）。

</details>

---

<a id="tech-stack"></a>
## 🛠 技術スタック

Python 3.11+ · PySide6（GUI） · pydantic v2 · SQLite · httpx（非同期） · rubymarshal ·
typer（CLI） · PyInstaller

---

<a id="license"></a>
## 📄 ライセンス

[MIT](LICENSE)

<div align="center">

<br>

🌐 [English](README.md) · [简体中文](README.zh-CN.md) · 日本語

</div>
