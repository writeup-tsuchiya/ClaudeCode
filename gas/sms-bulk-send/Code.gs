// ===============================================================
// 📱 SMS一括配信（ボタン起動版）
// ---------------------------------------------------------------
// ・テンプレートは「SMSテンプレート」シートから読み込む
// ・「送信リスト」シートに 名前 / 電話番号 を書いて、ボタンを押した瞬間に一括送信
// ・時間トリガーは使わない（押したときだけ動く）
//
// 主な安全装置
//   - 二重起動ロック（連打しても二重送信しない）
//   - 送信前に件数と本文プレビューを出して確認ダイアログ
//   - 「送信成功」の行は再実行してもスキップ（失敗行だけ再送できる）
//   - 1件送るたびにシートへ書き戻し（途中で止まっても送信済みが分かる）
//   - 実行時間5分で安全停止（残りは再度ボタンを押せば続きから）
// ===============================================================

// === シート名 ===
const SHEET_LIST     = '送信リスト';
const SHEET_TEMPLATE = 'SMSテンプレート';
const SHEET_SETTING  = 'システム設定';
const SHEET_LOG      = 'SMS送信ログ';

// === 自動で作られる結果列 ===
const HEADER_STATUS  = '送信ステータス';
const HEADER_SENT_AT = '送信日時';
const HEADER_API_RES = 'API応答';

// === 動作パラメータ ===
const SMS_MAX_LENGTH   = 660;          // 本文の最大文字数（超過分は切り捨て）
const SEND_INTERVAL_MS = 200;          // 1件ごとの送信間隔
const TIME_BUDGET_MS   = 5 * 60 * 1000; // 実行時間の上限（GASの6分制限対策）
const FLUSH_EVERY      = 10;           // 何件ごとにシートへ確定書き込みするか

const SMS_API_URL = 'https://sms-api.aossms.com/p5/api/mt.json';

// === ヘッダー名の候補（表記ゆれ吸収）===
const CAND = {
  target:    ['送信対象', '送信', '対象', 'チェック'],
  lastName:  ['お名前(姓)', '姓', '苗字', 'せい'],
  firstName: ['お名前(名)', '名', '下の名前', 'めい'],
  fullName:  ['お名前', '名前', '氏名', '担当者名', 'ご担当者名', '宛名', '会社名担当者'],
  phone:     ['電話番号', '電話', 'tel', '携帯', '携帯電話', '携帯番号', '連絡先'],
  template:  ['テンプレート', 'テンプレート名', '使用テンプレート', 'template'],
};

// テンプレートシートのヘッダー候補
const TMPL_CAND = {
  name: ['テンプレート名', 'テンプレート', 'ラベル', '名称', 'label'],
  body: ['本文', 'メッセージ', 'sms本文', 'body', 'message'],
};

// 設定シートのキー候補（A列にキー、B列に値）
const SETTING_KEYS = {
  token:      ['トークン', 'token', 'apiトークン'],
  clientId:   ['クライアントid', 'clientid', '顧客id'],
  smsCode:    ['smsコード', 'smscode', 'コード'],
  defTmpl:    ['既定テンプレート', 'デフォルトテンプレート', '標準テンプレート'],
};


// ===============================================================
// ① メニュー（スプレッドシートを開くと出る）
// ===============================================================
function onOpen() {
  const ui = getUi_();
  if (!ui) return;
  ui.createMenu('📱 SMS一括配信')
    .addItem('▶ 一括配信を実行', 'sendBulkSms')
    .addItem('👀 本文プレビュー（送信しない）', 'previewMessages')
    .addSeparator()
    .addItem('✉ テスト送信（1件だけ）', 'sendTestSms')
    .addItem('🔍 設定チェック', 'checkSettings')
    .addSeparator()
    .addItem('🧹 送信ステータスをクリア', 'clearSendStatus')
    .addItem('🛠 シートを初期化（初回のみ）', 'initSheets')
    .addToUi();
}


// ===============================================================
// ② 一括配信（← このファンクション名をボタンに割り当てる）
// ===============================================================
function sendBulkSms() {
  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(1000)) {
    alert_('実行中', '既に配信処理が動いています。終わるまでお待ちください。');
    return;
  }
  try {
    sendBulkSms_();
  } finally {
    lock.releaseLock();
  }
}

function sendBulkSms_() {
  const startedAt = Date.now();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ctx = readListContext_(ss);          // 送信リストの読み取り
  const cfg = readSettings_(ss);             // トークン等
  const tmplMap = readTemplates_(ss);        // テンプレート

  // --- 設定チェック（1件も送る前に落とす） ---
  const cfgErr = validateConfig_(cfg, tmplMap);
  if (cfgErr) {
    alert_('設定エラー', cfgErr);
    writeRunMemo_(ss, '設定エラーで中止: ' + cfgErr);
    return;
  }

  // --- 送信ジョブを組み立てる ---
  const { jobs, invalid, skipped } = buildJobs_(ctx, cfg, tmplMap);

  if (jobs.length === 0) {
    const msg = [
      '送信対象が0件でした。',
      '',
      `・スキップ（送信済み／対象外／空行）: ${skipped}件`,
      `・エラー（番号不正・テンプレ無し等）: ${invalid.length}件`,
      '',
      invalid.slice(0, 5).map(j => `  ${j.row}行目: ${j.error}`).join('\n'),
    ].join('\n');
    // エラー行はシートに理由を書いておく
    writeInvalidRows_(ctx, invalid);
    flushLogs_(ss, invalid.map(j => buildLog_(j, j.error, '')));
    alert_('送信対象なし', msg);
    return;
  }

  // --- 確認ダイアログ ---
  const preview = jobs[0];
  const dupCount = countDuplicatePhones_(jobs);
  const confirmMsg = [
    `${jobs.length}件のSMSを送信します。`,
    '',
    `・エラーでスキップ: ${invalid.length}件`,
    `・送信済み等でスキップ: ${skipped}件`,
    dupCount > 0 ? `・⚠ 電話番号の重複: ${dupCount}件（同じ番号に複数回届きます）` : '',
    '',
    '─ 1件目のプレビュー ─',
    `宛先: ${preview.displayName}（${preview.phone}）`,
    `テンプレート: ${preview.templateName}`,
    '',
    preview.body,
    '',
    '送信してよろしいですか？',
  ].filter(String).join('\n');

  if (!confirm_('SMS一括配信の確認', confirmMsg)) {
    writeRunMemo_(ss, `確認ダイアログでキャンセル（対象${jobs.length}件）`);
    return;
  }

  // --- エラー行を先に書き出す ---
  writeInvalidRows_(ctx, invalid);
  let logs = invalid.map(j => buildLog_(j, j.error, ''));

  // --- 送信ループ ---
  let okCount = 0, ngCount = 0, doneCount = 0;
  let timedOut = false;

  for (const job of jobs) {
    if (Date.now() - startedAt > TIME_BUDGET_MS) { timedOut = true; break; }

    const { ok, response } = sendSms_(cfg, job.phone, job.body);
    const status = ok ? '送信成功' : '送信失敗（APIエラー）';
    ok ? okCount++ : ngCount++;

    writeRowResult_(ctx, job.row, status, new Date(), response);
    logs.push(buildLog_(job, status, response));

    doneCount++;
    if (doneCount % FLUSH_EVERY === 0) {
      logs = flushLogs_(ss, logs);
      SpreadsheetApp.flush();
    }
    Utilities.sleep(SEND_INTERVAL_MS);
  }

  flushLogs_(ss, logs);
  SpreadsheetApp.flush();

  const rest = jobs.length - doneCount;
  const summary = [
    '📱 SMS一括配信が完了しました。',
    '',
    `・送信成功: ${okCount}件`,
    `・送信失敗: ${ngCount}件`,
    `・エラーでスキップ: ${invalid.length}件`,
    `・送信済み等でスキップ: ${skipped}件`,
    timedOut ? `\n⚠ 実行時間の上限に達したため ${rest}件が未送信です。\nもう一度ボタンを押すと続きから送信します。` : '',
  ].filter(String).join('\n');

  writeRunMemo_(ss, `実行完了 成功=${okCount} 失敗=${ngCount} エラー=${invalid.length} 未送信=${timedOut ? rest : 0}`);
  alert_('配信完了', summary);
}


// ===============================================================
// ③ 本文プレビュー（送信しない）
// ===============================================================
function previewMessages() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ctx = readListContext_(ss);
  const cfg = readSettings_(ss);
  const tmplMap = readTemplates_(ss);
  const { jobs, invalid, skipped } = buildJobs_(ctx, cfg, tmplMap);

  const lines = [
    `送信対象: ${jobs.length}件 / エラー: ${invalid.length}件 / スキップ: ${skipped}件`,
    '',
  ];

  jobs.slice(0, 3).forEach((j, i) => {
    lines.push(`─ ${i + 1}件目（${j.row}行目）─`);
    lines.push(`宛先: ${j.displayName}（${j.phone}）`);
    lines.push(`テンプレート: ${j.templateName}  本文${j.body.length}文字`);
    lines.push(j.body, '');
  });

  if (invalid.length) {
    lines.push('─ エラー行 ─');
    invalid.slice(0, 10).forEach(j => lines.push(`${j.row}行目: ${j.error}`));
  }

  alert_('本文プレビュー（送信していません）', lines.join('\n'));
}


// ===============================================================
// ④ テスト送信（1件だけ）
// ===============================================================
function sendTestSms() {
  const ui = getUi_();
  if (!ui) throw new Error('テスト送信はスプレッドシートのメニューから実行してください。');

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const cfg = readSettings_(ss);
  const tmplMap = readTemplates_(ss);

  const cfgErr = validateConfig_(cfg, tmplMap);
  if (cfgErr) { alert_('設定エラー', cfgErr); return; }

  const resPhone = ui.prompt('テスト送信', '送信先の電話番号を入力してください（例: 09012345678）', ui.ButtonSet.OK_CANCEL);
  if (resPhone.getSelectedButton() !== ui.Button.OK) return;
  const phone = normalizePhoneNumber_(resPhone.getResponseText());
  if (!phone) { alert_('エラー', '電話番号の形式が正しくありません。'); return; }

  const names = Object.keys(tmplMap);
  const defName = cfg.defTmpl && tmplMap[norm_(cfg.defTmpl)] ? cfg.defTmpl : names[0];
  const resTmpl = ui.prompt(
    'テスト送信',
    `使用するテンプレート名を入力してください。\n\n登録済み: ${names.map(k => tmplMap[k].name).join(' / ')}\n\n空欄なら「${defName}」を使います。`,
    ui.ButtonSet.OK_CANCEL
  );
  if (resTmpl.getSelectedButton() !== ui.Button.OK) return;

  const wantName = String(resTmpl.getResponseText() || defName).trim();
  const tmpl = tmplMap[norm_(wantName)];
  if (!tmpl) { alert_('エラー', `テンプレート「${wantName}」が見つかりません。`); return; }

  const body = renderBody_(tmpl.body, { '名前': 'テスト 太郎', '姓': 'テスト', '名': '太郎' });
  if (!confirm_('テスト送信の確認', `${phone} に送信します。\n\n${body}\n\nよろしいですか？`)) return;

  const { ok, response } = sendSms_(cfg, phone, body);
  flushLogs_(ss, [[new Date(), '', 'テスト送信', '', phone, tmpl.name, ok ? '送信成功' : '送信失敗（APIエラー）', body, response]]);
  alert_('テスト送信結果', (ok ? '✅ 送信成功' : '❌ 送信失敗') + '\n\nAPI応答:\n' + response);
}


// ===============================================================
// ⑤ 設定チェック
// ===============================================================
function checkSettings() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const lines = [];

  [SHEET_LIST, SHEET_TEMPLATE, SHEET_SETTING].forEach(name => {
    lines.push(`${ss.getSheetByName(name) ? '✅' : '❌'} シート「${name}」`);
  });

  if (!ss.getSheetByName(SHEET_LIST) || !ss.getSheetByName(SHEET_TEMPLATE) || !ss.getSheetByName(SHEET_SETTING)) {
    lines.push('', '不足しているシートがあります。メニューの「シートを初期化」を実行してください。');
    alert_('設定チェック', lines.join('\n'));
    return;
  }

  const cfg = readSettings_(ss);
  lines.push('');
  lines.push(`${cfg.token ? '✅' : '❌'} トークン`);
  lines.push(`${cfg.clientId ? '✅' : '❌'} クライアントID`);
  lines.push(`${cfg.smsCode ? '✅' : '❌'} SMSコード`);
  lines.push(`　既定テンプレート: ${cfg.defTmpl || '（未設定）'}`);

  const tmplMap = readTemplates_(ss);
  const tmplNames = Object.keys(tmplMap).map(k => tmplMap[k].name);
  lines.push('', `${tmplNames.length ? '✅' : '❌'} テンプレート ${tmplNames.length}件: ${tmplNames.join(' / ') || 'なし'}`);

  const ctx = readListContext_(ss);
  lines.push('', '─ 送信リストの列判定 ─');
  lines.push(`${ctx.idx.phone != null ? '✅' : '❌'} 電話番号列`);
  lines.push(`${(ctx.idx.fullName != null || ctx.idx.lastName != null) ? '✅' : '❌'} 名前列`);
  lines.push(`${ctx.idx.target != null ? '✅' : '－'} 送信対象チェック列${ctx.idx.target == null ? '（無い場合は全行が対象）' : ''}`);
  lines.push(`${ctx.idx.template != null ? '✅' : '－'} テンプレート列${ctx.idx.template == null ? '（無い場合は既定テンプレートを使用）' : ''}`);
  lines.push(`　データ行数: ${ctx.rows.length}行`);

  const { jobs, invalid, skipped } = buildJobs_(ctx, cfg, tmplMap);
  lines.push('', `送信対象: ${jobs.length}件 / エラー: ${invalid.length}件 / スキップ: ${skipped}件`);
  invalid.slice(0, 5).forEach(j => lines.push(`　${j.row}行目: ${j.error}`));

  alert_('設定チェック', lines.join('\n'));
}


// ===============================================================
// ⑥ 送信ステータスのクリア
// ===============================================================
function clearSendStatus() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ctx = readListContext_(ss);
  if (ctx.rows.length === 0) { alert_('クリア', 'データ行がありません。'); return; }

  if (!confirm_('送信ステータスのクリア',
    `${ctx.rows.length}行分の「${HEADER_STATUS}」「${HEADER_SENT_AT}」「${HEADER_API_RES}」を空にします。\n\n※クリアすると送信済みの行も再送対象になります。よろしいですか？`)) return;

  const blank = ctx.rows.map(() => ['']);
  [ctx.col.status, ctx.col.sentAt, ctx.col.apiRes].forEach(col => {
    ctx.sheet.getRange(2, col, ctx.rows.length, 1).setValues(blank);
  });
  writeRunMemo_(ss, `送信ステータスをクリア（${ctx.rows.length}行）`);
  alert_('クリア完了', '送信ステータスをクリアしました。');
}


// ===============================================================
// ⑦ シート初期化（初回セットアップ）
// ===============================================================
function initSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // --- 送信リスト ---
  let list = ss.getSheetByName(SHEET_LIST);
  if (!list) {
    list = ss.insertSheet(SHEET_LIST);
    const headers = ['送信対象', 'お名前(姓)', 'お名前(名)', '電話番号', 'テンプレート', HEADER_STATUS, HEADER_SENT_AT, HEADER_API_RES];
    list.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold').setBackground('#e8f0fe');
    list.setFrozenRows(1);
    list.getRange(2, 1, 200, 1).insertCheckboxes();
    list.getRange('A2:A200').setValue(true);
    list.getRange(2, 4, 200, 1).setNumberFormat('@'); // 電話番号は文字列（先頭0を守る）
    list.setColumnWidth(6, 160);
    list.setColumnWidth(8, 260);
  }

  // --- SMSテンプレート ---
  let tmpl = ss.getSheetByName(SHEET_TEMPLATE);
  if (!tmpl) {
    tmpl = ss.insertSheet(SHEET_TEMPLATE);
    tmpl.getRange(1, 1, 1, 3)
      .setValues([['テンプレート名', 'メモ', '本文']])
      .setFontWeight('bold').setBackground('#e8f0fe');
    tmpl.setFrozenRows(1);
    tmpl.getRange(2, 1, 2, 3).setValues([
      ['セミナー案内', '一斉案内用', '{{名前}}様\nお世話になっております。ライトアップです。\n本日のセミナーのご案内をお送りしました。ご確認ください。'],
      ['リマインド', '前日リマインド用', '{{名前}}様\n明日のお打ち合わせのリマインドです。よろしくお願いいたします。'],
    ]);
    tmpl.setColumnWidth(3, 480);
    tmpl.getRange(2, 3, 100, 1).setWrap(true);
  }

  // --- システム設定 ---
  let setting = ss.getSheetByName(SHEET_SETTING);
  if (!setting) {
    setting = ss.insertSheet(SHEET_SETTING);
    setting.getRange(1, 1, 1, 3)
      .setValues([['項目', '値', '説明']])
      .setFontWeight('bold').setBackground('#e8f0fe');
    setting.getRange(2, 1, 4, 3).setValues([
      ['トークン', '', 'SMS APIのtoken'],
      ['クライアントID', '', 'SMS APIのclientId'],
      ['SMSコード', '', 'SMS APIのsmsCode'],
      ['既定テンプレート', 'セミナー案内', '送信リストのテンプレート列が空のときに使う名前'],
    ]);
    setting.setColumnWidth(2, 240);
    setting.setColumnWidth(3, 320);
  }

  // --- 送信ログ ---
  ensureLogSheet_(ss);

  alert_('初期化完了', [
    '必要なシートを用意しました。',
    '',
    '1. 「システム設定」にトークン／クライアントID／SMSコードを入力',
    '2. 「SMSテンプレート」に本文を登録（{{名前}} が差し込みになります）',
    '3. 「送信リスト」に名前と電話番号を入力',
    '4. メニューまたはボタンから「一括配信を実行」',
  ].join('\n'));
}


// ===============================================================
// ⑧ SMS送信（API）
// ===============================================================
function sendSms_(cfg, phone, message) {
  const payload = {
    token: cfg.token,
    clientId: cfg.clientId,
    smsCode: cfg.smsCode,
    phoneNumber: phone,
    message: message,
  };
  const options = { method: 'post', payload: payload, muteHttpExceptions: true };

  let text;
  try {
    const res = UrlFetchApp.fetch(SMS_API_URL, options);
    text = res.getContentText() || '{}';
  } catch (e) {
    return { ok: false, response: 'FETCH_ERROR: ' + e.message };
  }

  try {
    const json = JSON.parse(text);
    return { ok: json.responseCode === 0, response: text };
  } catch (e) {
    return { ok: false, response: text };
  }
}


// ===============================================================
// ⑨ 送信ジョブの組み立て
// ===============================================================
function buildJobs_(ctx, cfg, tmplMap) {
  const jobs = [];
  const invalid = [];
  let skipped = 0;

  const tmplNames = Object.keys(tmplMap).map(k => tmplMap[k].name);
  const onlyOne = tmplNames.length === 1 ? tmplNames[0] : '';

  ctx.rows.forEach((row, i) => {
    const rowNo = i + 2; // 1行目はヘッダー

    // 完全な空行は無視
    if (row.every(v => String(v == null ? '' : v).trim() === '')) return;

    // 送信対象チェックが外れている行はスキップ
    if (ctx.idx.target != null && !isTruthy_(row[ctx.idx.target])) { skipped++; return; }

    // 送信済み（成功）はスキップ。失敗行はもう一度押せば再送される
    const status = String(cell_(row, ctx.idx.statusIdx)).trim();
    if (status.indexOf('送信成功') === 0) { skipped++; return; }

    const displayName = buildDisplayName_(row, ctx.idx);
    const rawPhone = String(cell_(row, ctx.idx.phone)).trim();

    const base = { row: rowNo, displayName: displayName, rawPhone: rawPhone };

    if (!rawPhone) {
      // 名前も電話も無ければ単なる空行扱い
      if (!displayName) { skipped++; return; }
      invalid.push(Object.assign({}, base, { phone: '', templateName: '', body: '', error: '送信失敗（電話番号が空）' }));
      return;
    }

    // --- テンプレート決定 ---
    const wantName = String(cell_(row, ctx.idx.template)).trim() || String(cfg.defTmpl || '').trim() || onlyOne;
    if (!wantName) {
      invalid.push(Object.assign({}, base, { phone: '', templateName: '', body: '', error: '送信失敗（使用テンプレートが未指定）' }));
      return;
    }
    const tmpl = tmplMap[norm_(wantName)];
    if (!tmpl) {
      invalid.push(Object.assign({}, base, { phone: '', templateName: wantName, body: '', error: `送信失敗（テンプレート「${wantName}」が無い）` }));
      return;
    }

    // --- 電話番号 ---
    const phone = normalizePhoneNumber_(rawPhone);
    if (!phone) {
      invalid.push(Object.assign({}, base, { phone: '', templateName: tmpl.name, body: '', error: '送信失敗（番号不正）' }));
      return;
    }

    // --- 本文 ---
    const body = renderBody_(tmpl.body, buildVars_(row, ctx));
    if (!body) {
      invalid.push(Object.assign({}, base, { phone: phone, templateName: tmpl.name, body: '', error: '送信失敗（本文が空）' }));
      return;
    }

    jobs.push(Object.assign({}, base, { phone: phone, templateName: tmpl.name, body: body }));
  });

  return { jobs: jobs, invalid: invalid, skipped: skipped };
}

// 差し込み変数を作る（全ヘッダー名 + 名前系の別名）
function buildVars_(row, ctx) {
  const vars = {};
  ctx.headers.forEach((h, i) => {
    const key = String(h == null ? '' : h).trim();
    if (key) vars[key] = String(row[i] == null ? '' : row[i]);
  });

  const last  = String(cell_(row, ctx.idx.lastName)).trim();
  const first = String(cell_(row, ctx.idx.firstName)).trim();
  const full  = String(cell_(row, ctx.idx.fullName)).trim() || (last + first);

  vars['名前']       = full;
  vars['お名前']     = full;
  vars['氏名']       = full;
  vars['ご担当者名'] = full;
  vars['姓']         = last || full;
  vars['名']         = first;
  return vars;
}

// {{キー}} を置換して文字数制限をかける
function renderBody_(template, vars) {
  const body = String(template || '').replace(/\{\{\s*([^}]+?)\s*\}\}/g, function (m, key) {
    const k = String(key).trim();
    if (Object.prototype.hasOwnProperty.call(vars, k)) return vars[k];
    // 表記ゆれ（全角/半角・スペース）でも拾う
    const nk = norm_(k);
    for (const vk in vars) {
      if (norm_(vk) === nk) return vars[vk];
    }
    return ''; // 未定義のプレースホルダは空にする（{{ }}を送らない）
  });
  return body.trim().slice(0, SMS_MAX_LENGTH);
}


// ===============================================================
// ⑩ シート読み取り
// ===============================================================
function readListContext_(ss) {
  const sheet = ss.getSheetByName(SHEET_LIST);
  if (!sheet) throw new Error(`シートが見つかりません: ${SHEET_LIST}\nメニューの「シートを初期化」を実行してください。`);

  // 結果列（無ければ作る）
  const colStatus = ensureHeaderExists_(sheet, HEADER_STATUS);
  const colSentAt = ensureHeaderExists_(sheet, HEADER_SENT_AT);
  const colApiRes = ensureHeaderExists_(sheet, HEADER_API_RES);

  const all = sheet.getDataRange().getDisplayValues();
  const headers = all.length ? all[0] : [];
  const rows = all.length > 1 ? all.slice(1) : [];
  const map = toColumnMap_(headers);

  const idx = {
    target:    findCol_(map, CAND.target),
    lastName:  findCol_(map, CAND.lastName),
    firstName: findCol_(map, CAND.firstName),
    fullName:  findCol_(map, CAND.fullName),
    phone:     findCol_(map, CAND.phone),
    template:  findCol_(map, CAND.template),
    statusIdx: colStatus - 1,
  };

  // 「お名前」しか無い場合、隣の列を名として救済
  if (idx.firstName == null && idx.lastName == null && idx.fullName != null) {
    const next = headers[idx.fullName + 1];
    if (next != null && /名/.test(String(next)) && !/電話|tel/i.test(String(next))) {
      idx.lastName = idx.fullName;
      idx.firstName = idx.fullName + 1;
    }
  }

  if (idx.phone == null) {
    throw new Error(`「${SHEET_LIST}」に電話番号の列が見つかりません。\nヘッダーを「電話番号」にしてください。`);
  }

  return {
    sheet: sheet,
    headers: headers,
    rows: rows,
    idx: idx,
    col: { status: colStatus, sentAt: colSentAt, apiRes: colApiRes },
  };
}

function readTemplates_(ss) {
  const sheet = ss.getSheetByName(SHEET_TEMPLATE);
  const map = {};
  if (!sheet || sheet.getLastRow() < 2) return map;

  const all = sheet.getRange(1, 1, sheet.getLastRow(), sheet.getLastColumn()).getDisplayValues();
  const headers = all[0];
  const hmap = toColumnMap_(headers);

  let iName = findCol_(hmap, TMPL_CAND.name);
  let iBody = findCol_(hmap, TMPL_CAND.body);
  if (iName == null) iName = 0;                       // 旧仕様：A列＝名前
  if (iBody == null) iBody = headers.length >= 3 ? 2 : 1; // 旧仕様：C列＝本文

  all.slice(1).forEach(r => {
    const name = String(r[iName] == null ? '' : r[iName]).trim();
    const body = String(r[iBody] == null ? '' : r[iBody]);
    if (name && body.trim()) map[norm_(name)] = { name: name, body: body };
  });
  return map;
}

function readSettings_(ss) {
  const sheet = ss.getSheetByName(SHEET_SETTING);
  const cfg = { token: '', clientId: '', smsCode: '', defTmpl: '' };
  if (!sheet || sheet.getLastRow() === 0) return cfg;

  const all = sheet.getRange(1, 1, sheet.getLastRow(), Math.max(2, sheet.getLastColumn())).getDisplayValues();

  // A列＝キー / B列＝値 の形式を優先
  let found = 0;
  all.forEach(r => {
    const key = norm_(r[0]);
    const val = String(r[1] == null ? '' : r[1]).trim();
    for (const field in SETTING_KEYS) {
      if (SETTING_KEYS[field].some(c => norm_(c) === key)) {
        cfg[field] = val;
        if (val) found++;
      }
    }
  });

  // 旧仕様（B4=token / C4=clientId / D4=smsCode）へのフォールバック
  if (found === 0 && all.length >= 4) {
    const r4 = all[3] || [];
    cfg.token    = String(r4[1] == null ? '' : r4[1]).trim();
    cfg.clientId = String(r4[2] == null ? '' : r4[2]).trim();
    cfg.smsCode  = String(r4[3] == null ? '' : r4[3]).trim();
  }
  return cfg;
}

function validateConfig_(cfg, tmplMap) {
  const miss = [];
  if (!cfg.token)    miss.push('トークン');
  if (!cfg.clientId) miss.push('クライアントID');
  if (!cfg.smsCode)  miss.push('SMSコード');
  if (miss.length) {
    return `「${SHEET_SETTING}」シートの次の項目が空です:\n\n・${miss.join('\n・')}`;
  }
  if (Object.keys(tmplMap).length === 0) {
    return `「${SHEET_TEMPLATE}」シートにテンプレートが1件もありません。\nテンプレート名と本文を入力してください。`;
  }
  return '';
}


// ===============================================================
// ⑪ シート書き込み
// ===============================================================
function writeRowResult_(ctx, rowNo, status, sentAt, apiRes) {
  const c = ctx.col;
  const api = String(apiRes || '').slice(0, 400);
  if (c.sentAt === c.status + 1 && c.apiRes === c.status + 2) {
    ctx.sheet.getRange(rowNo, c.status, 1, 3).setValues([[status, sentAt, api]]);
  } else {
    ctx.sheet.getRange(rowNo, c.status).setValue(status);
    ctx.sheet.getRange(rowNo, c.sentAt).setValue(sentAt);
    ctx.sheet.getRange(rowNo, c.apiRes).setValue(api);
  }
}

function writeInvalidRows_(ctx, invalid) {
  invalid.forEach(j => writeRowResult_(ctx, j.row, j.error, new Date(), ''));
}

function buildLog_(job, status, apiRes) {
  return [
    new Date(),
    job.row,
    job.displayName,
    job.rawPhone,
    job.phone || '',
    job.templateName || '',
    status,
    job.body || '',
    String(apiRes || '').slice(0, 400),
  ];
}

// ログを書き出して空配列を返す
function flushLogs_(ss, logs) {
  if (!logs || logs.length === 0) return [];
  const sheet = ensureLogSheet_(ss);
  sheet.getRange(sheet.getLastRow() + 1, 1, logs.length, 9).setValues(logs);
  return [];
}

function ensureLogSheet_(ss) {
  let sheet = ss.getSheetByName(SHEET_LOG);
  if (!sheet) sheet = ss.insertSheet(SHEET_LOG);
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, 9)
      .setValues([['ログ日時', '行番号', '宛名', '入力電話番号', '送信先番号', 'テンプレート', '送信ステータス', '本文', 'API応答']])
      .setFontWeight('bold').setBackground('#e8f0fe');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function writeRunMemo_(ss, note) {
  const sheet = ensureLogSheet_(ss);
  sheet.appendRow([new Date(), '', '', '', '', '', 'SYSTEM', String(note), '']);
}

// 指定ヘッダーが無ければ作る（空き列があればそこ、無ければ右端に追加）
function ensureHeaderExists_(sheet, name) {
  const target = norm_(name);
  const lastCol = Math.max(1, sheet.getLastColumn());
  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];

  let idx = headers.findIndex(h => norm_(h) === target);
  if (idx !== -1) return idx + 1;

  const emptyIdx = headers.findIndex(h => String(h == null ? '' : h).trim() === '');
  const col = (emptyIdx === -1) ? lastCol + 1 : emptyIdx + 1;
  sheet.getRange(1, col).setValue(name).setFontWeight('bold').setBackground('#e8f0fe');
  return col;
}


// ===============================================================
// ⑫ ヘルパー
// ===============================================================

// 表記ゆれ吸収（全角/半角・大小文字・空白を無視）
function norm_(s) {
  return String(s == null ? '' : s).normalize('NFKC').replace(/\s+/g, '').toLowerCase();
}

function toColumnMap_(headers) {
  const map = {};
  headers.forEach((h, i) => {
    const key = norm_(h);
    if (key !== '' && map[key] == null) map[key] = i;
  });
  return map;
}

function findCol_(map, candidates) {
  for (const name of candidates) {
    const idx = map[norm_(name)];
    if (idx != null) return idx;
  }
  return null;
}

function cell_(row, i) {
  return (i == null || row[i] == null) ? '' : row[i];
}

function buildDisplayName_(row, idx) {
  const full = String(cell_(row, idx.fullName)).trim();
  const last = String(cell_(row, idx.lastName)).trim();
  const first = String(cell_(row, idx.firstName)).trim();
  return (last + first) || full;
}

function isTruthy_(v) {
  const s = String(v == null ? '' : v).trim().toLowerCase();
  return s === 'true' || s === '1' || s === '○' || s === '◯' || s === '〇' ||
         s === 'yes' || s === 'y' || s === '送信' || s === 'x' || s === '✓' || s === 'v';
}

// 電話番号を +81 形式へ
function normalizePhoneNumber_(raw) {
  let tel = String(raw == null ? '' : raw).normalize('NFKC').replace(/[^0-9+]/g, '');
  if (/^[789]0\d{8}$/.test(tel)) tel = '0' + tel;   // 先頭0落ち（9012345678）の救済
  if (/^81\d{9,10}$/.test(tel)) tel = '+' + tel;    // 819012345678 の救済

  if (/^0\d{9,10}$/.test(tel)) return '+81' + tel.slice(1);
  if (/^\+81\d{9,10}$/.test(tel)) return tel;
  return '';
}

function countDuplicatePhones_(jobs) {
  const seen = {};
  let dup = 0;
  jobs.forEach(j => {
    if (seen[j.phone]) dup++;
    else seen[j.phone] = true;
  });
  return dup;
}

function getUi_() {
  try { return SpreadsheetApp.getUi(); } catch (e) { return null; }
}

function alert_(title, message) {
  const ui = getUi_();
  if (ui) ui.alert(title, message, ui.ButtonSet.OK);
  else Logger.log('[%s] %s', title, message);
}

function confirm_(title, message) {
  const ui = getUi_();
  if (!ui) {
    Logger.log('[%s] UIが無いため確認をスキップ: %s', title, message);
    return true;
  }
  return ui.alert(title, message, ui.ButtonSet.OK_CANCEL) === ui.Button.OK;
}
