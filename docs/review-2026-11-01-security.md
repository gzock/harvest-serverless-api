# 認証・メディア・法人管理レビュー（2026-11-01）

対象は旧版 `66d7d18` と復元後の実装です。今回の修正前状態は `3d51b00831ba946b5e07b145f4341f8474657f7c` を別ディレクトリへ展開して再現確認しました。認証・所属認可・写真・直接アップロード確定・テンプレート・Cognitoの従業員管理を確認し、実AWSへの操作や公開は行っていません。

## 再現して修正した問題

| 優先度 | 問題・成立条件 | 修正 | 根拠・回帰 |
| --- | --- | --- | --- |
| P1 | 直接アップロードで、旧実行のleaseが切れ、新実行が確定した写真を利用者が削除した後、旧実行が再開すると、写真配列が元に戻ったためtarget単独CASを通過し写真を復活させる。session更新は既に失敗しても写真側はコミット済みになる。新しい二段階確定処理に伴う競合。 | 写真登録・祖先カウンタ・session完了を同一DynamoDBトランザクションへ。sessionのphase、lease token、固定VersionId、期限を同時検査。旧方式で写真だけ保存済みのsessionは、既存写真のCASとsession確定で回復。 | `src/harvest/uploads.py:268`、`src/harvest/controllers/photo_controller.py`。`test_security_review_dynamodb.py` の旧実行停止→新実行確定→採用変更・写真削除→旧実行再開の実DBテスト。修正前は削除済み写真が再登録された。 |
| P1 | ロール変更・参加承認／拒否・除名が、先行認可後にprojectがimporting/deletingへ遷移しても書き込める。新しいprojectロックを一部writerが検査していなかった。 | 3操作すべてにactive project条件を同じトランザクションで適用。owner変更・削除禁止条件を維持。 | `src/harvest/controllers/role_controller.py:134`。状態2種×操作3種のDynamoDB Local負例。 |
| P1 | 小さいXLSX ZIPでも、worksheet dimension、セル座標、結合範囲、印刷範囲に非常に大きい値を指定でき、圧縮／展開バイト数だけではセル生成・矩形走査の上限にならない。旧版由来の範囲制限不足が復元後も残っていた。 | openpyxlで開く前にdefusedxmlでXMLを読み、座標・範囲・シート数・ワークブック全体のセル予算を検査。重複ZIP entryも拒否。 | `src/harvest/controllers/template_controller.py:203`。4KB未満のZIPによる大範囲指定を事前検査で拒否する5ケース。修正前の事前検査は受理した。OOMを起こす実行自体は行っていない。XML entityケースは既存のopenpyxl/defusedxml保護に加える事前拒否であり、新たなentity脆弱性の発見とは扱わない。 |
| P2 | Cognitoから削除済みのユーザーがRolesに残ると、`show_user(...)[0]` が失敗しメンバー一覧全体を取得できない。任意profile属性欠落でもKeyErrorになる。旧版由来。 | 所属レコードを残してuser IDを表示名の代替とし、email欠落は空文字。一覧と個別表示の既存レスポンス項目を維持。Cognito通信障害は「削除済み」と誤認せず伝播。 | `src/harvest/controllers/project_user_controller.py:30`、`test_security_review.py`。管理者が残存所属を表示・削除できる。通知経路は別担当のレビュー対象。 |
| P2 | 従業員作成後のpassword設定・group追加失敗で、新規アカウントが有効なまま残る。group追加前は通常の所属付き削除APIで補償できない。日時属性欠落で成功後に500になる場合もある。旧版由来。 | 作成成功応答で得た新規Usernameだけを直接無効化して補償。既存ユーザー競合時は無効化しない。補償失敗を明示し、optional日時を安全に除去。 | `src/harvest/controllers/corporation_controller.py:145`。password/group/billingの失敗3段階、既存ユーザー競合、日時欠落の回帰。 |
| P2 | テンプレートの繰り返し固定ラベルを重複placeholderと誤認し拒否する。一方、印刷範囲の列外にあるplaceholderは受理される。旧版由来。 | 重複数・位置の検査対象を既知placeholderだけに限定し、行と列の両方を検証。 | `src/harvest/controllers/template_controller.py:188`。固定ラベル重複の受理、列外placeholder拒否。 |

## 互換性・意図的な制約変更

- ID tokenの既存bare形式とBearer形式、厳密なRS256／issuer／audience／用途・有効期限検査を維持。import時のJWKS通信はありません。foreign photo/template、他法人従業員、owner保護の既存否定テストも通過しました。
- 写真の公開HTTP形式、server生成photo ID、固定S3 VersionId、各種別5枚上限は維持します。`PhotoController.create` に追加したfinalization引数は内部専用です。完了済みsessionの再送は、後に写真が削除されても新規登録しません。
- **テンプレートには旧版になかった上限を追加**しています。実セル／dimension／merge／印刷範囲は最大10,000行・256列、A1からの矩形走査予算は100,000セル、ワークブック全体にも100,000セルの予算、worksheetは32枚、処理する各worksheet/workbook XMLは8MiBまでです。以前からのZIP圧縮8MiB・展開64MiB・4,096entry制限も維持します。
- Excelが通常保存する「末尾列までの列スタイル」は実セルとは別に扱い、列スタイル定義はExcel上限の16,384列まで許可しています。配布済み `basic_1.xlsx` と `basic_2.xlsx` は新検査を通過します。これだけで、すべての実運用テンプレートの互換性を保証するものではありません。大きい既存テンプレートは移行前に新制約で検証が必要です。

## 検証結果

修正前の隔離コードに新しい回帰テストを適用すると、unitは **12 failed / 4 passed**、DynamoDB Localは **7 failed / 2 passed** となり、今回扱った問題を再現しました。

修正後は関連unit **121 passed**、新DynamoDB Localテスト **9 passed**。既存quota／leaseテストも、新しいphoto内部契約に合わせて完了actionを実DBへ適用するmockへ更新し、新テストと合わせて **10 passed**。テンプレート全体予算の最終変更後、review unit **16 passed** を再確認しました。対象6 sourceのmypyと対象source/testsのRuffは通過しています。全体ビルド・全pytest・契約確認は親担当が統合後に実行します。

Localの競合テストではS3だけを差し替え、写真とsessionのDynamoDBトランザクションは実際に実行します。実Cognito、実S3、IAMの適用結果はLocal／mockだけでは検証できません。独立担当が追加session Updateの既存IAM範囲とlease／serializationを読解レビューしています。

## 残る制約

- テンプレートの「5件まで」「同名不可」は依然S3 list→putで判定しており、同時アップロードでは厳密な原子保証がありません。旧版由来の制約として残し、今回新たなregistry/reservation用永続資源は導入していません。
- S3 putとDynamoDBは同一トランザクションにできません。失効した実行によるS3孤立オブジェクトは残り得ますが、その実行によるDB参照の再作成を防止しました。未知のコミット結果で参照中の写真を消さない方針と、既存のstorage reconciliation手順を維持します。
- Cognitoアカウント作成そのものの応答喪失や、プロセス終了で補償コードが動かないケースは、この同期処理だけでは自動復旧できません。無効化に失敗した場合も管理者による復旧が必要です。請求の月内人数仕様を変更したり、未知の課金結果を推測して減額したりはしません。
- JWTの署名・期限検証はCognito側の失効状態を逐次問い合わせる仕組みではありません。全トークンの即時失効を保証する変更は行っていません。
