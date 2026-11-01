# 管理操作

`create_tables.sh`、`create_corp_user.sh`、`create_billing_destination.sh` は `scripts/admin.py` の互換入口です。`set -euo pipefail` とlock済みPython環境を使います。引数なしでは対象を推測せず終了し、通常はJSON計画だけを出力します。dry-runではAWS接続・credential取得を行いません。`--apply` を付けた場合だけ書き込みます。本変更の検証ではAWSへ書き込んでいません。

AWS向けには `--environment`、`--region`、`--account-id` が必須です。`--profile` は任意で、短命のSSO/session資格情報を利用できます。applyは最初にSTSで実際のアカウントを照合します。local向けは `--environment local --endpoint-url http://127.0.0.1:18000` を明示し、loopback以外を拒否します。

## テーブル

```sh
./create_tables.sh --environment local --region ap-northeast-1 \
  --endpoint-url http://127.0.0.1:18000 --table-prefix harvest_dev_
```

計画を確認し、同じ引数に `--apply` を追加して作成します。localでは従来の6テーブルにImports、UploadSessions、GenerationJobsを加え、必要なindex・Streams・TTLを作成します。AWS向けは既存構成の6テーブルだけを対象とし、新3テーブルはSAMが所有・作成します。管理スクリプトでは二重作成しません。各アプリの `TABLE_*`、`UPLOAD_SESSIONS_TABLE`、`GENERATION_JOBS_TABLE` は実際の作成名へ設定してください。既存テーブルを削除・置換・データコピーする機能はありません。既存schema不一致は移行設計が必要です。

AWS上の既存資源やSAMが所有する資源の作成には通常この入口を使わず、デプロイの変更セット／resource importを使用してください。この入口で新規AWSテーブルを作る場合は、prefix、バックアップ、ownership、SAMパラメータとの対応を先に確認します。

## 請求先

ローカルのJSONファイルへ `user_id`、`name`、`email`、`postal_code`、`address`、`department`、`tel`、`contact` の文字列を指定します。引用符・改行・日本語はJSONとして処理します。

```sh
./create_billing_destination.sh --environment staging --region ap-northeast-1 \
  --account-id "$AWS_ACCOUNT_ID" --table "$BILLS_TABLE_NAME" \
  --input /secure/path/billing-destination.json
```

計画確認後に `--apply` を追加します。既存の請求先は条件付き書き込みで保護し、上書きしません。既存値の変更はアプリの管理APIか、レビューした条件付き更新で行ってください。入力ファイルとdry-run出力には個人情報があるため、GitやCIログへ保存せず権限制限した場所で扱います。

## 法人アカウント

ローカルJSONへ `username`、`email`、`name`、`kana_name`、`pricing_plan` のみを指定します。password、既存pool ID、実在する認証情報を設定例へ埋め込みません。

```sh
./create_corp_user.sh --environment staging --region ap-northeast-1 \
  --account-id "$AWS_ACCOUNT_ID" --user-pool-id "$COGNITO_USER_POOL_ID" \
  --input /secure/path/corporation.json
```

apply時はCognito生成の初回パスワードを招待メールで配信し、ユーザーが初回ログインで変更します。メールを未検証のまま作成し、検証フローを省略しません。poolのcustom属性、メール送信、初回パスワード変更・MFAフローを事前に確認してください。既存ユーザーのパスワードや属性を自動更新する機能はありません。

作成結果の `created_username` と**完全に同名**の法人Cognito groupを、管理者のレビュー済みIaC／管理操作で用意してください。このgroupがない状態では法人管理APIを利用できません。groupは従業員の境界なので、既存の別法人groupを流用しません。ユーザー作成後に後続の手順が失敗した場合は、そのユーザーを確認して続きから復旧し、同名で無条件に再作成しないでください。

旧workspace検証スクリプトは [archive/experiments](../archive/experiments/README.md) に非実行資料として保存しました。デプロイは引き続き `deploy.sh` とその成果物・変更セット確認手順を使います。
