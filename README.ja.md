# miduo

[English](README.md)

MusicXML / MuseScore楽譜を、2本の単旋律声部へ自動編曲するPython CLIである。
主旋律を第1声に残し、原曲の伴奏から和声の骨格を表す第2声の旋律線を
選ぶ。

編曲アルゴリズムは汎用2声を志向しているが、現行実装の音域と出力楽器設定は
ヴァイオリン向けである。第1声をG3–E7、第2声をG3–A6に制限し、出力パートの
楽器メタデータもヴァイオリンとして書き出す。これらは将来、選択可能な
声部別の楽器プロファイルへ拡張する想定である。

対応する入出力形式は `.musicxml`、`.xml`、`.mxl`、`.mscz` である。
MSCZの読み書きにはMuseScoreのCLIを使用する。

## 必要環境

- Python 3.11以上
- [uv](https://docs.astral.sh/uv/)
- MuseScore 3または4（MSCZを扱う場合のみ）

依存環境を準備し、CLIを確認する。

```console
uv sync
uv run miduo --help
```

`uv.lock` は再現可能な開発・CI環境のためリポジトリへコミットする。

## 編曲する

```console
uv run miduo arrange input.musicxml -o duet.musicxml
uv run miduo arrange input.mscz -o duet.mscz
uv run miduo arrange input.mscz -o duet-music21.mscz \
  --harmony-backend music21
```

和声解析は高速な `internal`（既定）と、局所調性・ローマ数字を扱う
`music21` の2方式から選べる。同じ曲を両方で生成し、聴き比べることを
推奨する。

`arrange` は次の処理を順に実行する。

1. MusicXMLの解析
2. 和声スライスとコードの推定
3. 持続音・掛留音・ペダル音の検出
4. 2声への割当
5. 第2声のリズム簡略化
6. 音域・単声性・声部交差の検証
7. MusicXML / MXL / MSCZの書き出し

長い曲でも停止と誤解しにくいよう、処理段階と声部割当中の小節進捗を
標準エラーへ逐次表示する。

```text
[parse] 400 measures, 3870 note events
[harmony] analyzing 2419 slices
[assign-voices] measure 214 (215/400, 54%)
[assign-voices] measure 399 (400/400, 100%)
[validate] valid, 0 retries
```

進捗表示を止めるには `--quiet`、出力せず全パイプラインを確認するには
`--dry-run` を指定する。

```console
uv run miduo arrange input.mscz -o duet.mscz --quiet
uv run miduo arrange input.mscz -o duet.musicxml --dry-run
```

MuseScoreを自動検出できない場合は実行ファイルを指定できる。

```console
uv run miduo arrange input.mscz -o duet.mscz \
  --musescore "/Applications/MuseScore 4.app/Contents/MacOS/mscore"
```

パッケージをインストールせずに実行する場合:

```console
PYTHONPATH=src python -m miduo --help
```

## 中間結果を調べる

各段階を個別に実行でき、多くのコマンドは `--json` に対応する。

```console
uv run miduo inspect input.mscz
uv run miduo parse input.musicxml --json
uv run miduo slice input.musicxml --json
uv run miduo analyze input.musicxml --json
uv run miduo analyze input.musicxml --harmony-backend music21 --json
uv run miduo spans input.musicxml --json
uv run miduo assign input.musicxml --json
uv run miduo reduce input.musicxml --json
uv run miduo validate input.musicxml --json
```

`analyze` と `spans` / `assign` では `--confidence-threshold`、
`reduce` では `--attack-threshold`、`validate` では `--max-retries`
を調整できる。

## 2声への圧縮方法

原曲を音の開始・終了位置で短い和声スライスに分割する。第1声には
第1パートの第1声部を主旋律として割り当て、第2声には伴奏音とその
オクターブ移動から候補を作る。現在は第1声をG3–E7、第2声をG3–A6に
制限する。

`internal` バックエンドは独自のコードテンプレート照合である。長三和音、
短三和音、増三和音、属七、長七、短七、減七、半減七を推定し、低信頼区間は
直前の確信度が高いコードで補間する。

`music21` バックエンドは同じスライスをmusic21へ渡し、16四分音符ごとの
局所調性、コードルート、ローマ数字を推定する。記譜上の調号も補助情報に
使い、V–Iおよび二次的ドミナントの解決をカデンツとして扱う。music21が
明確なコード種別を返せない区間では、内部推定器へフォールバックする。

第2声は、和声情報の欠落、音域外、跳躍、声部交差、声部間隔、持続音の
切断、弱拍での過剰な動きなどをコスト化して選ぶ。カデンツ、無音、
または四分音符8個分を境界とする区間ごとに、休符を含む候補を最大8選択肢、
経路を最大24本へ絞る状態マージ付きビームサーチである。

詳細は [DESIGN.ja.md](DESIGN.ja.md) を参照する。

## 現在の制約

- 編曲パーサは `score-partwise` MusicXMLを対象とする
- 主旋律は第1パートの最初の声部と仮定する
- 出力は各声部1音のみで、同一声部内の重音には対応しない
- グレースノート、強弱、アーティキュレーション、歌詞などは出力へ引き継がない
- 出力音価は可読性とMuseScore互換性のため16分音符グリッドへ量子化する
- コード推定は局所的なピッチ集合に基づき、調性・転調・機能和声を大域解析しない
- music21の局所調性は16四分音符単位の近似で、転調位置を厳密には求めない
- 原曲にない対旋律を新しく作曲するのではなく、原曲音から骨格を選択する

## テスト

```console
uv run pytest
uv run ruff check .
```
