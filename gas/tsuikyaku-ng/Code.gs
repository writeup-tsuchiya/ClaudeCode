/**
 * 精査元スプレッドシート：C列の名称が対象リストに一致したら、G列のプルダウンを「追客NG」にする。
 *
 * 使い方は同ディレクトリの README.md を参照。
 *   - applyTsuikyakuNg()   … シート全体を一括反映（メニュー／手動実行）
 *   - previewTsuikyakuNg() … 書き換えずに対象行だけログ出力（ドライラン）
 *   - onEdit(e)            … C列を編集／貼り付けした瞬間に同じ判定を実行
 */

// ===== 設定 =====================================================================
var CONFIG = {
  // 精査元スプレッドシート。コンテナバインドで使う場合は空文字のままでOK（開いているブックを対象にする）。
  spreadsheetId: '1li2epxdbOUk87DuHgg9NKgQAize__EUp8-E5BogqtWI',

  // 対象シート。URL の #gid=... の数値。gid で見つからない場合は sheetName を使う。
  sheetGid: 1403156942,
  sheetName: '',

  nameColumn: 3,        // 名称が入っている列（C列）
  targetColumn: 7,      // プルダウンを書き換える列（G列）
  targetValue: '追客NG', // 書き込む値
  startRow: 2,          // データ開始行（1行目がヘッダーなら 2）

  // 一致判定の方式
  //   'normalized' … 表記ゆれ（全角半角・空白・株式会社などの法人格）を吸収した完全一致【推奨】
  //   'partial'    … 上記に加えて「C列の値の中に対象名称を含む」場合も一致とみなす
  //   'exact'      … 文字列そのままの完全一致
  matchMode: 'normalized',

  // G列にすでに別の値が入っていても上書きするか。false なら空欄の行だけ書き込む。
  overwriteExisting: true,

  // G列のプルダウン（入力規則）に「追客NG」が無い場合、選択肢に自動追加するか。
  // false のときは警告をログに出すだけ（値はセットされるが、セルに無効マークが付くことがある）。
  autoExtendDropdown: false
};

// C列がこの名称のとき G列を「追客NG」にする
var NG_NAMES = [
  '尼崎信用金庫',
  '株式会社肥後銀行',
  '埼玉りそな銀行',
  'りそなグループ',
  '東京海上日動火災保険株式会社',
  '港区',
  '宇都宮市'
];
// ===============================================================================

/** スプレッドシートを開いたときにメニューを追加する（簡易トリガー） */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('追客NG判定')
    .addItem('C列を精査してG列を一括更新', 'applyTsuikyakuNg')
    .addItem('対象行をプレビュー（書き換えない）', 'previewTsuikyakuNg')
    .addSeparator()
    .addItem('自動更新トリガーを設置', 'createOnEditTrigger')
    .addItem('自動更新トリガーを削除', 'deleteOnEditTriggers')
    .addToUi();
}

/**
 * シート全体を精査して、対象行の G列を「追客NG」にする。
 * @return {{scanned: number, matched: number, updated: number}} 処理件数
 */
function applyTsuikyakuNg() {
  return runTsuikyakuNg_(false);
}

/** 書き換えずに、対象になる行と現在の G列の値をログに出す（ドライラン）。 */
function previewTsuikyakuNg() {
  return runTsuikyakuNg_(true);
}

/**
 * C列の編集（手入力・貼り付け）に反応して、その行の G列を「追客NG」にする。
 * すでに別の onEdit がある場合は、この関数の中身（handleEdit_ の呼び出し）を移植してください。
 * @param {GoogleAppsScript.Events.SheetsOnEdit} e
 */
function onEdit(e) {
  handleEdit_(e);
}

/** インストーラブルトリガー用のエントリポイント（createOnEditTrigger で設置） */
function onEditInstallable(e) {
  handleEdit_(e);
}

/** onEdit のインストーラブルトリガーを設置する（保護シート等で簡易トリガーが効かない場合に使用） */
function createOnEditTrigger() {
  deleteOnEditTriggers();
  ScriptApp.newTrigger('onEditInstallable')
    .forSpreadsheet(openSpreadsheet_())
    .onEdit()
    .create();
  Logger.log('onEdit トリガーを設置しました。');
}

/** このスクリプトが設置した onEdit トリガーを削除する */
function deleteOnEditTriggers() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'onEditInstallable') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
}

// ===== 本体 =====================================================================

/**
 * 一括処理の本体。
 * @param {boolean} dryRun true ならログ出力のみで書き込まない
 * @return {{scanned: number, matched: number, updated: number}}
 */
function runTsuikyakuNg_(dryRun) {
  var sheet = getTargetSheet_();
  var lastRow = sheet.getLastRow();
  var result = { scanned: 0, matched: 0, updated: 0 };

  if (lastRow < CONFIG.startRow) {
    Logger.log('データ行がありません。');
    return result;
  }

  var numRows = lastRow - CONFIG.startRow + 1;
  var names = sheet.getRange(CONFIG.startRow, CONFIG.nameColumn, numRows, 1).getValues();
  var current = sheet.getRange(CONFIG.startRow, CONFIG.targetColumn, numRows, 1).getValues();
  var matcher = buildMatcher_();

  result.scanned = numRows;

  var rowsToUpdate = [];
  for (var i = 0; i < numRows; i++) {
    var name = names[i][0];
    if (!matcher(name)) continue;
    result.matched++;

    var row = CONFIG.startRow + i;
    var now = current[i][0];
    if (String(now).trim() === CONFIG.targetValue) continue;                 // すでに追客NG
    if (!CONFIG.overwriteExisting && String(now).trim() !== '') {            // 既存値を残す設定
      Logger.log('スキップ（既存値あり） 行' + row + ' : ' + name + ' / G=' + now);
      continue;
    }
    rowsToUpdate.push({ row: row, name: name, before: now });
  }

  for (var j = 0; j < rowsToUpdate.length; j++) {
    Logger.log((dryRun ? '[プレビュー] ' : '') + '行' + rowsToUpdate[j].row +
      ' : ' + rowsToUpdate[j].name + ' / G「' + rowsToUpdate[j].before + '」→「' + CONFIG.targetValue + '」');
  }

  if (!dryRun && rowsToUpdate.length > 0) {
    ensureDropdownValue_(sheet, rowsToUpdate[0].row);
    var rows = rowsToUpdate.map(function (r) { return r.row; });
    writeValueToRows_(sheet, rows, CONFIG.targetValue);
    result.updated = rows.length;
  }

  var message = '精査 ' + result.scanned + '行 / 対象 ' + result.matched + '件 / ' +
    (dryRun ? '更新予定 ' + rowsToUpdate.length : '更新 ' + result.updated) + '件';
  Logger.log(message);
  toast_(message);
  return result;
}

/**
 * onEdit 共通処理。編集範囲が C列にかかっていれば、その行の G列を書き換える。
 * @param {GoogleAppsScript.Events.SheetsOnEdit} e
 */
function handleEdit_(e) {
  if (!e || !e.range) return;

  var sheet = e.range.getSheet();
  if (!isTargetSheet_(sheet)) return;

  var firstCol = e.range.getColumn();
  var lastCol = e.range.getLastColumn();
  if (CONFIG.nameColumn < firstCol || CONFIG.nameColumn > lastCol) return; // C列を含まない編集

  var firstRow = Math.max(e.range.getRow(), CONFIG.startRow);
  var lastRow = e.range.getLastRow();
  if (lastRow < firstRow) return;

  var numRows = lastRow - firstRow + 1;
  var names = sheet.getRange(firstRow, CONFIG.nameColumn, numRows, 1).getValues();
  var current = sheet.getRange(firstRow, CONFIG.targetColumn, numRows, 1).getValues();
  var matcher = buildMatcher_();

  var rows = [];
  for (var i = 0; i < numRows; i++) {
    if (!matcher(names[i][0])) continue;
    var now = String(current[i][0]).trim();
    if (now === CONFIG.targetValue) continue;
    if (!CONFIG.overwriteExisting && now !== '') continue;
    rows.push(firstRow + i);
  }
  if (rows.length === 0) return;

  ensureDropdownValue_(sheet, rows[0]);
  writeValueToRows_(sheet, rows, CONFIG.targetValue);
}

// ===== 一致判定 =================================================================

/**
 * CONFIG.matchMode に応じた判定関数を作る。
 * @return {function(*): boolean} C列の値を渡すと対象かどうかを返す関数
 */
function buildMatcher_() {
  var rawTargets = [];
  var normTargets = [];
  for (var i = 0; i < NG_NAMES.length; i++) {
    var raw = String(NG_NAMES[i]).trim();
    if (raw === '') continue;
    rawTargets.push(raw);
    normTargets.push(normalizeName_(raw));
  }

  if (CONFIG.matchMode === 'exact') {
    return function (value) {
      var v = String(value == null ? '' : value).trim();
      if (v === '') return false;
      return rawTargets.indexOf(v) !== -1;
    };
  }

  var partial = (CONFIG.matchMode === 'partial');
  return function (value) {
    var v = normalizeName_(value);
    if (v === '') return false;
    for (var i = 0; i < normTargets.length; i++) {
      var t = normTargets[i];
      if (t === '') continue;
      if (v === t) return true;
      if (partial && v.indexOf(t) !== -1) return true;
    }
    return false;
  };
}

/**
 * 表記ゆれを吸収する。全角半角・大文字小文字・空白・記号・法人格（株式会社など）を落とす。
 * 例）「（株）肥後銀行」「株式会社 肥後銀行」「㈱肥後銀行」→ いずれも「肥後銀行」
 * @param {*} value
 * @return {string}
 */
function normalizeName_(value) {
  var s = String(value == null ? '' : value);
  if (s === '') return '';

  // 丸囲みの法人格を先に正式表記へ（NFKC で「(株)」に化ける前に処理する）
  s = s.replace(/㈱/g, '株式会社').replace(/㈲/g, '有限会社')
       .replace(/㈳/g, '社団法人').replace(/㈶/g, '財団法人')
       .replace(/㈻/g, '学校法人').replace(/㈷/g, '社会福祉法人');

  // 全角英数字・記号を半角へ、半角カナを全角へ（Apps Script は normalize('NFKC') が使える）
  if (typeof s.normalize === 'function') {
    s = s.normalize('NFKC');
  }

  // 括弧付きの略記も正式表記へ（この時点で括弧は半角になっている）
  s = s.replace(/\(株\)/g, '株式会社').replace(/\(有\)/g, '有限会社')
       .replace(/\(同\)/g, '合同会社').replace(/\(資\)/g, '合資会社').replace(/\(名\)/g, '合名会社')
       .replace(/\(一社\)/g, '一般社団法人').replace(/\(一財\)/g, '一般財団法人')
       .replace(/\(公社\)/g, '公益社団法人').replace(/\(公財\)/g, '公益財団法人')
       .replace(/\(医\)/g, '医療法人').replace(/\(学\)/g, '学校法人').replace(/\(福\)/g, '社会福祉法人');

  s = s.toLowerCase();

  // 空白（全角スペース含む）と、区切りに使われがちな記号を除去
  s = s.replace(/[\s\u3000]/g, '');
  s = s.replace(/[()（）「」【】・･,，.。\-ー―‐_/\\]/g, '');

  // 法人格そのものを除去（長い表記から順に消す）
  var legalForms = [
    '特定非営利活動法人', '一般社団法人', '一般財団法人', '公益社団法人', '公益財団法人',
    '社会福祉法人', '医療法人社団', '医療法人財団', '医療法人', '学校法人', '宗教法人',
    '独立行政法人', '国立大学法人', '事業協同組合', '協同組合',
    '株式会社', '有限会社', '合同会社', '合資会社', '合名会社',
    '社団法人', '財団法人', 'npo法人'
  ];
  for (var i = 0; i < legalForms.length; i++) {
    s = s.split(legalForms[i]).join('');
  }
  return s;
}

// ===== シート・書き込み =========================================================

/** @return {GoogleAppsScript.Spreadsheet.Spreadsheet} 対象スプレッドシート */
function openSpreadsheet_() {
  if (CONFIG.spreadsheetId) {
    return SpreadsheetApp.openById(CONFIG.spreadsheetId);
  }
  var active = SpreadsheetApp.getActiveSpreadsheet();
  if (!active) {
    throw new Error('スプレッドシートを特定できません。CONFIG.spreadsheetId を設定してください。');
  }
  return active;
}

/** @return {GoogleAppsScript.Spreadsheet.Sheet} gid（無ければシート名）で特定した対象シート */
function getTargetSheet_() {
  var ss = openSpreadsheet_();
  var sheets = ss.getSheets();

  if (CONFIG.sheetGid !== null && CONFIG.sheetGid !== undefined && CONFIG.sheetGid !== '') {
    for (var i = 0; i < sheets.length; i++) {
      if (sheets[i].getSheetId() === Number(CONFIG.sheetGid)) return sheets[i];
    }
  }
  if (CONFIG.sheetName) {
    var byName = ss.getSheetByName(CONFIG.sheetName);
    if (byName) return byName;
  }
  throw new Error('対象シートが見つかりません（gid=' + CONFIG.sheetGid + ' / name=' + CONFIG.sheetName + '）。' +
    'CONFIG.sheetGid か CONFIG.sheetName を確認してください。');
}

/**
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @return {boolean} 編集されたシートが対象シートか
 */
function isTargetSheet_(sheet) {
  if (CONFIG.sheetGid !== null && CONFIG.sheetGid !== undefined && CONFIG.sheetGid !== '') {
    return sheet.getSheetId() === Number(CONFIG.sheetGid);
  }
  if (CONFIG.sheetName) return sheet.getName() === CONFIG.sheetName;
  return true;
}

/**
 * 指定行の対象列に同じ値を書き込む。連続する行はまとめて setValues する。
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {Array.<number>} rows 昇順の行番号
 * @param {string} value
 */
function writeValueToRows_(sheet, rows, value) {
  if (rows.length === 0) return;
  var start = rows[0];
  var length = 1;

  for (var i = 1; i <= rows.length; i++) {
    if (i < rows.length && rows[i] === rows[i - 1] + 1) {
      length++;
      continue;
    }
    var block = [];
    for (var j = 0; j < length; j++) block.push([value]);
    sheet.getRange(start, CONFIG.targetColumn, length, 1).setValues(block);
    if (i < rows.length) {
      start = rows[i];
      length = 1;
    }
  }
  SpreadsheetApp.flush();
}

/**
 * G列のプルダウン（入力規則）に「追客NG」が含まれているか確認する。
 * 含まれていない場合、CONFIG.autoExtendDropdown が true なら選択肢に追加し、false なら警告を出す。
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} sampleRow 入力規則を確認する行
 */
function ensureDropdownValue_(sheet, sampleRow) {
  var cell = sheet.getRange(sampleRow, CONFIG.targetColumn);
  var rule = cell.getDataValidation();
  if (!rule) return; // プルダウン無しならそのまま書き込める

  if (rule.getCriteriaType() !== SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) return;

  var values = rule.getCriteriaValues()[0] || [];
  for (var i = 0; i < values.length; i++) {
    if (String(values[i]).trim() === CONFIG.targetValue) return; // 選択肢にある
  }

  if (!CONFIG.autoExtendDropdown) {
    Logger.log('警告: G列のプルダウンに「' + CONFIG.targetValue + '」がありません。' +
      '値はセットされますが、セルに無効マークが付く場合があります。' +
      '選択肢を自動追加するには CONFIG.autoExtendDropdown を true にしてください。');
    return;
  }

  var extended = values.slice();
  extended.push(CONFIG.targetValue);
  var newRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(extended, true)
    .setAllowInvalid(rule.getAllowInvalid())
    .setHelpText(rule.getHelpText() || '')
    .build();

  var lastRow = Math.max(sheet.getLastRow(), CONFIG.startRow);
  sheet.getRange(CONFIG.startRow, CONFIG.targetColumn, lastRow - CONFIG.startRow + 1, 1)
    .setDataValidation(newRule);
  Logger.log('G列のプルダウンに「' + CONFIG.targetValue + '」を追加しました。');
}

/** 画面右下にトーストを出す（UIが無い実行環境では何もしない） */
function toast_(message) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    if (ss) ss.toast(message, '追客NG判定', 5);
  } catch (err) {
    // トリガー実行などUIが無い場合は無視
  }
}
