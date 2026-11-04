/**
 * analytics_collector.gs — сборщик посещений витрины.
 *
 * Это НЕ часть бота. Код живёт в Google Apps Script и принимает
 * отправки со страницы, складывая их строками в вашу Google-таблицу.
 *
 * Зачем так. Сайт лежит на GitHub Pages: он отдаёт готовые файлы и
 * ничего не записывает, журнала обращений у вас нет. Нужен кто-то,
 * кто примет данные. Можно было бы поставить сторонний счётчик, но
 * тогда данные о ваших потенциальных клиентах окажутся у чужой
 * компании. Apps Script бесплатен, данные остаются в вашей таблице.
 *
 * КАК РАЗВЕРНУТЬ (десять минут, один раз):
 *
 *  1. Зайдите на script.google.com, нажмите «Новый проект».
 *  2. Удалите всё, что там есть, и вставьте этот файл целиком.
 *  3. Сверху нажмите «Сохранить», назовите проект, например
 *     «Ras Laffan analytics».
 *  4. Нажмите «Начать развёртывание» → «Новое развёртывание».
 *     Тип выберите «Веб-приложение».
 *     «Запуск от имени» — от вашего имени.
 *     «У кого есть доступ» — ОБЯЗАТЕЛЬНО «У всех».
 *     Иначе браузер посетителя не сможет отправить данные.
 *  5. Нажмите «Развернуть». Google попросит разрешения — согласитесь.
 *  6. Скопируйте адрес веб-приложения. Он выглядит так:
 *     https://script.google.com/macros/s/AKfyc.../exec
 *  7. Вставьте его в config.py в строку ANALYTICS_ENDPOINT.
 *  8. Запустите python bot.py publish — и счётчик заработает.
 *
 * Таблица создастся сама при первой отправке. Найти её потом: в
 * редакторе Apps Script слева «Ресурсы» или просто поищите в Google
 * Диске по названию из SHEET_NAME.
 *
 * ПРОВЕРКА. Откройте адрес веб-приложения в браузере — должно
 * ответить "ok". Если просит войти в аккаунт, значит на шаге 4 не
 * выбрано «У всех».
 */

var SHEET_NAME = 'Ras Laffan visits';

/** Колонки. Порядок менять можно, но тогда почистите старую таблицу. */
var HEADERS = [
  'time',          // когда пришла отправка
  'site',          // какой монитор, если их несколько
  'session',       // склейка двух отправок одного визита
  'event',         // view — заход, exit — уход
  'org',           // организация по сети, если включено
  'country',
  'city',
  'referrer',      // откуда пришёл
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'seconds',       // сколько пробыл
  'scroll',        // до какого места долистал, %
  'exit_section',  // на каком разделе ушёл
  'read',          // что читал и сколько секунд
  'clicks',        // звонок, почта, форма
  'mobile',
  'lang',
  'screen',
  'tz',
  'path'
];


function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var sheet = getSheet_();
    sheet.appendRow(HEADERS.map(function (h) {
      if (h === 'time') { return new Date(); }
      if (h === 'referrer') { return data.ref || ''; }
      var v = data[h];
      return (v === undefined || v === null) ? '' : v;
    }));
    return ContentService.createTextOutput('ok');
  } catch (err) {
    // Молча терять отправки нельзя: иначе однажды окажется, что
    // счётчик месяц ничего не пишет, и причины не найти.
    logError_(err);
    return ContentService.createTextOutput('error');
  }
}


/** Открытие адреса в браузере — простая проверка, что всё живо. */
function doGet() {
  return ContentService.createTextOutput('ok');
}


function getSheet_() {
  var id = PropertiesService.getScriptProperties().getProperty('sheetId');
  var ss;
  if (id) {
    try { ss = SpreadsheetApp.openById(id); } catch (e) { ss = null; }
  }
  if (!ss) {
    ss = SpreadsheetApp.create(SHEET_NAME);
    PropertiesService.getScriptProperties().setProperty('sheetId', ss.getId());
  }
  var sheet = ss.getSheets()[0];
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}


function logError_(err) {
  try {
    var ss = SpreadsheetApp.openById(
      PropertiesService.getScriptProperties().getProperty('sheetId'));
    var log = ss.getSheetByName('errors') || ss.insertSheet('errors');
    log.appendRow([new Date(), String(err).slice(0, 500)]);
  } catch (e) { /* уже ничем не помочь */ }
}


/**
 * Сводка за сутки на почту.
 *
 * Необязательно. Если хотите получать письмо каждое утро, поставьте
 * эту функцию на триггер: в редакторе слева «Триггеры» → «Добавить
 * триггер» → функция dailySummary, источник «По времени», тип
 * «Ежедневно», час на выбор.
 */
function dailySummary() {
  var sheet = getSheet_();
  var rows = sheet.getDataRange().getValues();
  if (rows.length < 2) { return; }

  var head = rows[0];
  var col = {};
  head.forEach(function (h, i) { col[h] = i; });

  var since = new Date();
  since.setDate(since.getDate() - 1);

  var visits = {}, orgs = {}, refs = {}, exits = {}, clicks = 0;
  var seconds = [], scrolls = [];

  for (var i = 1; i < rows.length; i++) {
    var r = rows[i];
    if (!(r[col.time] instanceof Date) || r[col.time] < since) { continue; }
    visits[r[col.session]] = true;
    if (r[col.event] === 'view') {
      if (r[col.org]) { orgs[r[col.org]] = (orgs[r[col.org]] || 0) + 1; }
      var ref = r[col.referrer] || 'direct';
      refs[ref] = (refs[ref] || 0) + 1;
    }
    if (r[col.event] === 'exit') {
      if (r[col.seconds]) { seconds.push(Number(r[col.seconds])); }
      if (r[col.scroll]) { scrolls.push(Number(r[col.scroll])); }
      var ex = r[col.exit_section] || '(unknown)';
      exits[ex] = (exits[ex] || 0) + 1;
      if (r[col.clicks]) { clicks++; }
    }
  }

  function median(a) {
    if (!a.length) { return 0; }
    a.sort(function (x, y) { return x - y; });
    return a[Math.floor(a.length / 2)];
  }
  function top(obj) {
    return Object.keys(obj).sort(function (a, b) { return obj[b] - obj[a]; })
      .slice(0, 8).map(function (k) { return '  ' + k + ' — ' + obj[k]; })
      .join('\n') || '  —';
  }

  var body = [
    'Визитов за сутки: ' + Object.keys(visits).length,
    'Медианное время на странице: ' + median(seconds) + ' сек',
    'Медианная глубина прокрутки: ' + median(scrolls) + '%',
    'Визитов с кликом по звонку/почте/форме: ' + clicks,
    '',
    'Организации:',
    top(orgs),
    '',
    'Откуда пришли:',
    top(refs),
    '',
    'На каком разделе уходили:',
    top(exits)
  ].join('\n');

  MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
                    'Ras Laffan: посещения за сутки', body);
}
