# Harvest serverless API

AWS SAM / Lambda / API Gateway / DynamoDB / S3 / Cognitoで構成するHarvest APIです。プロジェクト、場所、撮影対象、写真、テンプレート、帳票生成、通知、法人管理・課金を扱います。フロントエンドは`harvest-webapp`で管理します。

最新の差分レビュー・修正・再検証結果は [2026-11-01の自己レビュー](docs/self-review-2026-11-01.md) を参照してください。

横断的な変更と残課題はwebappリポジトリの`docs/modernization/implementation-status.md`、Python依存・成果物の検証記録は [runtime-validation.md](docs/runtime-validation.md) を参照してください。旧構成を記録した初回監査とは区別しています。

## 開発環境

- Python 3.14（`.python-version`参照）
- uv 0.12.23以上（CIは0.12.23に固定）
- SAM CLI 1.169.0（`uv tool run`でアプリ依存と分離）

```sh
uv sync --frozen --group dev
uv run --frozen ruff check
uv run --frozen mypy
uv run --frozen python scripts/export_contract.py --check
uv run --frozen pytest
```

依存は`pyproject.toml`と`uv.lock`で管理します。開発時もCIもlockに従ってインストールします。テストはAWSリソースを変更せず、モックと合成データで実行します。既存の`workspace/`内スクリプトは検証用の古いコードを含むため、開発の初期化手順として実行しないでください。

## ビルド

```sh
make build
python3 scripts/artifact_manifest.py verify --artifact .
```

`make build`はlockからLambda用Linux依存を`.build/lambda`へまとめ、handler import検証、IaC検査、SAM buildの成功後に`.build/lambda-manifest.json`を作ります。manifestは全ファイルとbuilt templateのSHA-256を記録し、混在・欠落・書換えを検出します。署名や配布元の真正性を証明するものではありません。ローカルOSのsite-packagesをそのままLambdaへコピーしないでください。SAMはSkipBuildを使い、`.aws-sam/build/template.yaml`から`../../.build/lambda`を参照します。配布・保存時は`.build/lambda`、`.build/lambda-manifest.json`、`.aws-sam/build/template.yaml`をこの相対構成で保持してください。

`make package`や`sam build`単独では新しいmanifestは作成しません。配布用には`make build`を使います。seal後にartifactを追加検査する際は`PYTHONDONTWRITEBYTECODE=1`を設定し、`__pycache__`を生成しないでください。変更が必要ならソースから再buildし、以前のmanifestを新しいファイルへ使い回さないでください。

CIはfrozen install、Ruff、型検査、OpenAPI生成差分、pytest、package、SAMの検証・ビルドを実行し、レポートと成果物を保存します。DynamoDB Localをimage digest固定で起動し、条件付き更新やtransactionの統合テストも実行します。本番へのデプロイは実行しません。型検査の範囲・厳格さは`pyproject.toml`のmypy設定を参照してください。

ローカルで同じ統合テストを実行する場合はCIに記載したDynamoDB Local imageをloopbackの18000番へ起動し、`DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:18000 uv run --frozen pytest tests/integration`を実行します。endpoint未指定時は統合テストだけskipします。テーブル名は各実行で分離し、テスト後に削除します。実AWS endpointは指定しないでください。

## APIをローカル実行する場合

SAMのローカル実行にはDockerが必要です。

```sh
uv tool run --from aws-sam-cli==1.169.0 sam local start-api
```

フロントエンドの開発API URLは`http://127.0.0.1:3000`です。SAMの実行だけではDynamoDB/S3/Cognitoはローカル化されません。`dev_env.json`や各関数の環境変数を専用開発環境に合わせて用意し、利用するAWS資源と権限を確認してください。unitテストやフロントのmock E2EにはSAM、Docker、AWS資格情報は不要です。

## デプロイと運用

デプロイ先のstack、artifact bucket、region、環境変数は`deploy.sh`と`template.yaml`の指定を確認してください。変更セットで既存データ資源の置換がないことを確認し、stagingで認証・権限・画像・帳票のsmoke testを行ってから同一成果物を昇格します。AWSの認証情報はコードや設定例に記載せず、環境別の短命資格情報を使います。

`deploy.sh`はCIから取得した保存済み成果物を使用し、再ビルドしません。既定はローカルmanifest検証とコマンド表示のみです。`python3`の標準ライブラリで検証し、成功するまでSAMを呼びません。以下の`DIRECTORY`には上記3要素を配置します。**manifest導入前に保存したartifactは拒否されます。新しい`make build`で再生成してください。**

```sh
./deploy.sh --stack-name YOUR_STACK --s3-bucket YOUR_ARTIFACT_BUCKET \
  --region ap-northeast-1 --artifact DIRECTORY \
  --parameter-overrides EnvironmentName=staging CorsOrigin=https://web.example.com
# template.yamlの必須parameterをすべて指定し、実行時だけ --execute を追加
```

実行時にもSAMの変更セット確認を維持します。CloudFormationで管理していない既存資源はparameterで参照し、変更セットと資源の実設定を別途確認してください。rollbackにも同じ成果物形式を使い、導入済みデータと互換な版を選びます。

`EnvironmentName=dev|staging|prod`を1回だけ明示します。未指定・重複を拒否し、stack名から環境を推測しません。旧版のimplicit `ServerlessRestApi`/`Prod`から、現行の明示`HarvestApi`/`EnvironmentName`への変更はAPI ID・URLの維持を保証しません。旧新APIの並行staging確認、custom domain mapping/runtime config切替、旧writer/event処理のdrainは[切替レビュー](docs/review-cutover-2026-11-01.md)を参照してください。

`create_tables.sh`、ユーザー／請求先作成スクリプトはAWSへ書き込む管理操作です。通常のセットアップやテストの一部として自動実行しません。復元手順とバックアップを用意したうえで、対象環境を確認して運用します。

## 変更時の回帰確認

権限・tenant境界、DynamoDBのpagination/条件付き更新、課金の日付境界、CSV文字コード、画像サイズ・形式、XLSXセル・画像anchor・改ページ、非同期イベントの重複を重点確認します。帳票はバイナリ全体の一致だけで判定せず、構造と代表出力を確認します。実Cognitoの認証、AWS IAM、実帳票の印刷結果はmockテストだけでは保証されないため、専用環境での確認を行ってください。
