/**
 * 商談ログ分析結果 追記用 Web API（Google Apps Script）
 *
 * 目的:
 *   Claude Code の Routine には「既存スプレッドシートに行を追記する」手段が無い
 *   （Google Drive コネクタは読み取りとファイル新規作成しかできない）。
 *   そこでスプレッドシート側に追記用のエンドポイントを立て、Routine から HTTPS で叩く。
 *
 * デプロイ手順は docs/shodan-review-setup.md を参照。
 *
 * エンドポイント:
 *   POST /  ... 分析結果を担当者タブに追記する（fileId で重複排除）
 *   GET  /?action=processed[&owner=浦島]
 *           ... 追記済みの fileId 一覧を返す。Routine が「分析済みログ」を判定するために使う。
 *   GET  /?action=ping
 *           ... 疎通確認。
 */

/** 追記先スプレッドシート。別ファイルに切り替える場合はここだけ変更する。 */
const SPREADSHEET_ID = '1t7pVp895kKB0vWWHbq-h_hfDAp0HkW5oc1cW20HeCtQ';

/** 担当者タブの列定義。順番を変えたら既存タブのヘッダーも直すこと。 */
const HEADERS = [
  '分析日時',
  '商談日',
  '担当者',
  '会社名',
  'Aヨミ',
  '失注ステータス',
  '商談評価(点)',
  '改善点',
  '次回アクション',
  'ログファイル名',
  'ログURL',
  'fileId',
];

/** fileId 列の位置（1始まり）。重複排除に使う。 */
const FILE_ID_COL = HEADERS.indexOf('fileId') + 1;

/** processed 応答で 1 タブあたり返す最大件数（新しいものから）。 */
const PROCESSED_LIMIT = 500;

// ---------------------------------------------------------------- entry points

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    assertToken_(body.token);

    const rows = body.rows;
    if (!Array.isArray(rows) || rows.length === 0) {
      return json_({ ok: false, error: 'rows が空です' });
    }

    const lock = LockService.getScriptLock();
    lock.waitLock(30000);
    try {
      const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
      const byOwner = groupBy_(rows, function (r) { return String(r.owner || '未割当').trim(); });
      const result = { ok: true, appended: 0, skipped: [], tabs: {} };

      Object.keys(byOwner).forEach(function (owner) {
        const sheet = getOrCreateSheet_(ss, owner);
        const known = processedIds_(sheet);
        const fresh = [];

        byOwner[owner].forEach(function (r) {
          const fileId = String(r.fileId || '').trim();
          if (!fileId) {
            result.skipped.push({ owner: owner, reason: 'fileId が無い', fileName: r.fileName || '' });
            return;
          }
          if (known.has(fileId)) {
            result.skipped.push({ owner: owner, reason: '追記済み', fileId: fileId });
            return;
          }
          known.add(fileId);
          fresh.push(toRow_(r, owner, fileId));
        });

        if (fresh.length > 0) {
          sheet
            .getRange(sheet.getLastRow() + 1, 1, fresh.length, HEADERS.length)
            .setValues(fresh);
          result.appended += fresh.length;
          result.tabs[owner] = fresh.length;
        }
      });

      SpreadsheetApp.flush();
      return json_(result);
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
}

function doGet(e) {
  try {
    const params = (e && e.parameter) || {};
    assertToken_(params.token);

    if (params.action === 'ping') {
      return json_({ ok: true, spreadsheetId: SPREADSHEET_ID });
    }

    if (params.action === 'processed') {
      const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
      const sheets = params.owner
        ? [ss.getSheetByName(String(params.owner).trim())].filter(Boolean)
        : ss.getSheets();
      const out = {};
      sheets.forEach(function (sheet) {
        const ids = Array.from(processedIds_(sheet));
        out[sheet.getName()] = ids.slice(Math.max(0, ids.length - PROCESSED_LIMIT));
      });
      return json_({ ok: true, processed: out });
    }

    return json_({ ok: false, error: '未知の action です（ping / processed）' });
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
}

// ---------------------------------------------------------------- helpers

function assertToken_(token) {
  const expected = PropertiesService.getScriptProperties().getProperty('API_TOKEN');
  if (!expected) throw new Error('スクリプトプロパティ API_TOKEN が未設定です');
  if (String(token || '') !== expected) throw new Error('トークンが一致しません');
}

/** 担当者タブを取得。無ければヘッダー付きで作る。 */
function getOrCreateSheet_(ss, owner) {
  let sheet = ss.getSheetByName(owner);
  if (sheet) return sheet;

  sheet = ss.insertSheet(owner);
  sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS])
    .setFontWeight('bold')
    .setBackground('#1f6b45')
    .setFontColor('#ffffff');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(HEADERS.indexOf('改善点') + 1, 320);
  sheet.setColumnWidth(HEADERS.indexOf('次回アクション') + 1, 320);
  sheet.hideColumns(FILE_ID_COL);
  return sheet;
}

/** そのタブに追記済みの fileId 集合。 */
function processedIds_(sheet) {
  const last = sheet.getLastRow();
  if (last < 2) return new Set();
  const values = sheet.getRange(2, FILE_ID_COL, last - 1, 1).getValues();
  const set = new Set();
  values.forEach(function (v) {
    const id = String(v[0] || '').trim();
    if (id) set.add(id);
  });
  return set;
}

function toRow_(r, owner, fileId) {
  return [
    r.analyzedAt || new Date(),
    r.meetingDate || '',
    owner,
    r.company || '',
    r.yomi || '',
    r.lostStatus || '',
    r.score === 0 || r.score ? r.score : '',
    r.improvements || '',
    r.nextAction || '',
    r.fileName || '',
    r.logUrl || ('https://drive.google.com/file/d/' + fileId + '/view'),
    fileId,
  ];
}

function groupBy_(items, keyFn) {
  return items.reduce(function (acc, item) {
    const key = keyFn(item);
    (acc[key] = acc[key] || []).push(item);
    return acc;
  }, {});
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
