/**
 * 「送信mailログ」自動追記スクリプト
 *
 * Drive の「ヒロシ送信キュー」フォルダに置かれた CSV を読み、
 * スプレッドシートの「送信mailログ」シートへ行を追加する。
 * 取り込んだ CSV は「処理済み」サブフォルダへ移動するので、二重取り込みは起きない。
 *
 * 導入手順:
 *   1. 商談情報統合ヨミ表を開く
 *   2. 拡張機能 → Apps Script
 *   3. このファイルの中身を全部貼り付けて保存
 *   4. 「importQueue」を選んで一度実行し、権限を承認する
 *   5. 時計アイコン（トリガー）→ トリガーを追加
 *      関数: importQueue / イベントのソース: 時間主導型
 *      タイプ: 日付ベースのタイマー / 時刻: 午前6時〜7時
 *
 * 注意: スプレッドシートの所有者アカウントで設定すること。
 */

var QUEUE_FOLDER_ID = '1tlwhtOwSh8J6_N8xaPNdNoUm3BRyXUfq';
var SHEET_NAME = '送信mailログ';
var DONE_FOLDER_NAME = '処理済み';
var EXPECTED_COLUMNS = 14; // A〜N

function importQueue() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    throw new Error('シート「' + SHEET_NAME + '」が見つかりません');
  }

  var queue = DriveApp.getFolderById(QUEUE_FOLDER_ID);
  var done = getOrCreateSubfolder(queue, DONE_FOLDER_NAME);

  // 既に台帳にある宛先は取り込まない（同じ CSV を手動で置き直しても安全）
  var existing = existingRecipients(sheet);

  var files = queue.getFiles();
  var imported = 0;
  var skipped = 0;

  while (files.hasNext()) {
    var file = files.next();
    if (file.getName().slice(-4).toLowerCase() !== '.csv') continue;

    var rows = Utilities.parseCsv(file.getBlob().getDataAsString('UTF-8'));
    var toAppend = [];

    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row.length < 5) continue;              // 空行
      if (row[0] === '作成日') continue;          // ヘッダー行が混ざっていた場合
      if (row.length !== EXPECTED_COLUMNS) {
        throw new Error(file.getName() + ' の ' + (i + 1) + '行目が' +
                        row.length + '列です（' + EXPECTED_COLUMNS + '列必要）');
      }
      var recipient = String(row[3]).trim().toLowerCase();
      if (recipient && existing[recipient]) { skipped++; continue; }
      existing[recipient] = true;
      toAppend.push(row);
    }

    if (toAppend.length > 0) {
      sheet.getRange(sheet.getLastRow() + 1, 1, toAppend.length, EXPECTED_COLUMNS)
           .setValues(toAppend);
      imported += toAppend.length;
    }

    file.moveTo(done);
  }

  if (imported > 0 || skipped > 0) {
    Logger.log('取り込み ' + imported + '件 / 重複スキップ ' + skipped + '件');
  }
  return { imported: imported, skipped: skipped };
}

function existingRecipients(sheet) {
  var last = sheet.getLastRow();
  var seen = {};
  if (last < 2) return seen;
  var values = sheet.getRange(2, 4, last - 1, 1).getValues(); // D列=送信先
  for (var i = 0; i < values.length; i++) {
    var v = String(values[i][0]).trim().toLowerCase();
    if (v) seen[v] = true;
  }
  return seen;
}

function getOrCreateSubfolder(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}
