// Run in the console of a public page at https://zakupki.gov.ru/epz/.
(() => {
  "use strict";

  const origin = "https://zakupki.gov.ru";
  if (location.origin !== origin || !location.pathname.startsWith("/epz/")) {
    throw new Error("Откройте публичную страницу ЕИС на zakupki.gov.ru/epz/.");
  }
  if (document.getElementById("kamchatka-eis")) return;

  const road = [
    "08383000001", 'МУНИЦИПАЛЬНОЕ КАЗЕННОЕ УЧРЕЖДЕНИЕ "СЛУЖБА АВТОМОБИЛЬНЫХ ДОРОГ ПЕТРОПАВЛОВСК-КАМЧАТСКОГО ГОРОДСКОГО ОКРУГА"',
    "4101143436", "410101001", "1114101001057",
  ];
  const amenities = [
    "03383000001", 'МУНИЦИПАЛЬНОЕ КАЗЕННОЕ УЧРЕЖДЕНИЕ "СЛУЖБА БЛАГОУСТРОЙСТВА ПЕТРОПАВЛОВСК-КАМЧАТСКОГО ГОРОДСКОГО ОКРУГА"',
    "4101118486", "410101001", "1074101005660",
  ];
  const plant = [
    "05386000012", 'МУНИЦИПАЛЬНОЕ УНИТАРНОЕ ПРЕДПРИЯТИЕ ПЕТРОПАВЛОВСК-КАМЧАТСКОГО ГОРОДСКОГО ОКРУГА "СПЕЦДОРРЕМСТРОЙ"',
    "4101108209", "410101001", "1064101050034",
  ];
  const registration = ([code, name, inn, kpp, ogrn], role = "-1") =>
    [`${code}:${name}`, code, code, "", inn, role, kpp, ogrn].join("zZ");
  const search = (path, params) => {
    const url = new URL(path, origin);
    url.search = new URLSearchParams({
      morphology: "on", pageNumber: "1", recordsPerPage: "_50",
      sortDirection: "false", showLotsInfoHidden: "false", ...params,
    });
    return url;
  };
  const municipalities = [registration(road, ""), registration(amenities)].join("|||");
  const contract44 = search("/epz/contract/search/results.html", {
    fz44: "on", contractStageList: "0,1,2,3", contractStageList_0: "on",
    contractStageList_1: "on", contractStageList_2: "on", contractStageList_3: "on",
    sortBy: "UPDATE_DATE", customerIdOrg: municipalities,
  });
  const contract223 = search("/epz/contractfz223/search/results.html", {
    sortBy: "BY_UPDATE_DATE", currencyId: "-1", customerIdOrg: registration(plant),
  });
  const notices = search("/epz/order/extendedsearch/results.html", {
    fz44: "on", fz223: "on", af: "on", ca: "on", pc: "on", pa: "on",
    sortBy: "UPDATE_DATE", currencyIdGeneral: "-1",
    customerIdOrg: [road, amenities, plant].map((org) => registration(org)).join("|||"),
  });
  const sources = [
    {name: "Контракты 44-ФЗ", kind: "44", url: contract44},
    {name: "Договоры 223-ФЗ", kind: "223", url: contract223},
    {name: "Извещения 44-ФЗ и 223-ФЗ", kind: "notice", url: notices},
  ];

  const clean = (value) => (value ?? "").replace(/\s+/g, " ").trim();
  const text = (node) => clean(node?.textContent);
  const lines = (node) => [...node.querySelectorAll("a[href]")];
  const date = (value) => {
    const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value ?? "");
    return m ? `${m[3]}-${m[2]}-${m[1]}` : null;
  };
  const dateByLabel = (body, label) => date(body.match(
    new RegExp(`${label}\\s+(\\d{2}\\.\\d{2}\\.\\d{4})`, "u"),
  )?.[1]);
  const selectedLinks = (node, pattern) => [...new Set(lines(node)
    .map((link) => new URL(link.getAttribute("href"), origin))
    .filter((url) => url.origin === origin && pattern.test(url.pathname))
    .map((url) => url.href))];

  function readList(page, kind) {
    const rows = [];
    for (const card of page.querySelectorAll(".search-registry-entry-block")) {
      const body = text(card);
      const candidates = selectedLinks(card, kind === "notice"
        ? /^\/epz\/order\/notice\/.*\/common-info\.html$/
        : /^\/epz\/(?:contract\/contractCard\/common-info|contractfz223\/card\/contract-info)\.html$/);
      const sourceUrl = candidates[0];
      if (!sourceUrl) continue;
      const id = new URL(sourceUrl).searchParams.get(kind === "notice" ? "regNumber" : "reestrNumber");
      if (!/^\d{11,24}$/.test(id ?? "")) continue;
      const header = text(card.querySelector(".registry-entry__header"));
      const placement = dateByLabel(body, "Размещено");
      const start = dateByLabel(body, "Заключение (?:контракта|договора)");
      const end = kind === "223"
        ? date(body.match(/Срок исполнения\s+\d{2}\.\d{2}\.\d{4}\s*[—–-]\s*(\d{2}\.\d{2}\.\d{4})/)?.[1])
        : dateByLabel(body, "Срок исполнения");
      const subject = body.match(/(?:Объект закупки|Объекты закупки)\s+(.+?)(?=\s+(?:Организация, осуществляющая размещение|Цена контракта|Цена договора|Посмотреть все)|$)/u)?.[1];
      rows.push({id, source_url: sourceUrl, header, summary: subject ?? null,
        published_on: placement, signed_on: start, performance_end_on: end});
    }
    const total = text(page.querySelector(".search-results__total"));
    const next = Number(page.querySelector(".paginator-button-next[data-pagenumber]")?.dataset.pagenumber);
    const empty = /Поиск не дал результатов/u.test(text(page.body));
    const exact = /^(\d[\d ]*)\s+запис/u.exec(total);
    return {rows, total, expected: exact ? Number(exact[1].replaceAll(" ", "")) : null, next, empty};
  }

  function fields(page) {
    const fields = new Map();
    for (const label of page.querySelectorAll(".section__title")) {
      const value = text(label.nextElementSibling);
      if (value && !fields.has(text(label))) fields.set(text(label), value);
    }
    return fields;
  }

  function isAccessError(error) {
    return /HTTP (401|403|429)|ограничила доступ|перенаправила запрос/.test(String(error));
  }

  function readContract(page, row, kind) {
    const values = fields(page);
    const subjectLabel = kind === "44" ? "Предмет контракта" : "Предмет договора";
    const plainLabel = [...page.querySelectorAll("span, div")]
      .find((node) => node.children.length === 0 && text(node) === subjectLabel);
    const subject = values.get(subjectLabel) || text(plainLabel?.nextElementSibling);
    if (!text(page.body).includes(row.id)) {
      throw new Error("В ответе отсутствует карточка запрошенного контракта.");
    }
    const suppliers = new Map();
    // The organisation is the first cell. Other cells contain contact details.
    for (const cell of page.querySelectorAll("td.tableBlock__col_first")) {
      const match = /^(.+?)\s*ИНН:\s*(\d{10}|\d{12})\b/u.exec(text(cell));
      if (match) suppliers.set(match[2], {name: match[1].trim(), inn: match[2]});
    }
    const related = selectedLinks(page, /^\/epz\/order\/notice\/.*\/common-info\.html$/)
      .filter((url) => !url.includes("/rpec/"))
      .find((url) => /^\d{11}$|^\d{19}$/.test(new URL(url).searchParams.get("regNumber") ?? ""));
    return {
      ...row, subject: subject || row.summary, suppliers: [...suppliers.values()],
      notice_id: related ? new URL(related).searchParams.get("regNumber") : null,
      notice_url: related ?? null, status: values.get("Статус контракта") ?? row.header,
      price: values.get("Цена контракта") ?? values.get("Цена договора") ?? null,
      signed_on: date(values.get("Дата заключения контракта") ?? values.get("Дата заключения договора")) ?? row.signed_on,
      performance_end_on: date(values.get("Дата окончания исполнения контракта")) ?? row.performance_end_on,
      place: values.get("Место поставки товара, выполнения работы или оказания услуги") ?? null,
      object_links: selectedLinks(page, /\/(?:payment-info-and-target-of-order|contract-subject)\.html$/),
      attachment_tabs: selectedLinks(page, /\/document-info\.html$/),
    };
  }

  async function getPage(url, signal) {
    signal.throwIfAborted();
    const abort = new AbortController();
    const forward = () => abort.abort(signal.reason);
    signal.addEventListener("abort", forward, {once: true});
    const timeout = setTimeout(() => abort.abort(), 30000);
    try {
      const response = await fetch(url, {signal: abort.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (new URL(response.url).origin !== origin || !new URL(response.url).pathname.startsWith("/epz/")) {
        throw new Error("ЕИС перенаправила запрос за пределы публичных страниц.");
      }
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      if (/доступ ограничен|подтвердите, что вы не робот|access denied/i.test(text(page.body))) {
        throw new Error("ЕИС ограничила доступ. Сохраните частичный результат.");
      }
      return page;
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener("abort", forward);
    }
  }
  const pace = () => new Promise((resolve) => setTimeout(resolve, 850));

  function inPeriod(row, from, to, kind) {
    if (kind === "notice") return !row.published_on || (row.published_on >= from && row.published_on <= to);
    return (!row.signed_on || row.signed_on <= to) && (!row.performance_end_on || row.performance_end_on >= from);
  }

  function readSupplyLines(page) {
    return [...page.querySelectorAll("tr")].map((row) => [...row.cells].map(text))
      .filter((cells) => cells.length >= 5 && /^\d+$/.test(cells[0]) && /\d{2}\.\d{2}/.test(cells[2]))
      .map(([position, name, okpd2, quantity, unit_price]) => ({
        position, name: name.split("Тип объекта закупки:")[0].trim(), okpd2, quantity, unit_price,
      }));
  }

  async function collect(source, {sample, files, from, to}, result, signal, progress) {
    const rows = new Set();
    const report = {name: source.name, source_url: source.url.href, visited: 0, found: 0,
      in_period: 0, errors: [], exhausted: false};
    result.coverage.push(report);
    const listUrl = new URL(source.url);
    let pageNumber = 1;
    while (pageNumber) {
      progress(`${source.name} · страница ${pageNumber} · просмотрено ${report.found}`);
      listUrl.searchParams.set("pageNumber", pageNumber);
      await pace();
      let page;
      try {
        page = readList(await getPage(listUrl, signal), source.kind);
        if (!page.rows.length && !page.empty) throw new Error("Карточки не найдены в HTML выдачи.");
      } catch (error) {
        if (signal.aborted) throw error;
        report.errors.push({url: listUrl.href, message: String(error)});
        if (isAccessError(error)) throw error;
        return;
      }
      report.visited++;
      report.public_total = page.total;
      const unseen = page.rows.filter((row) => !rows.has(row.id));
      if (page.rows.length && !unseen.length) {
        report.errors.push({url: listUrl.href, message: "ЕИС вернула повторяющуюся страницу."});
        return;
      }
      for (const row of unseen) rows.add(row.id);
      report.found = rows.size;
      const selected = unseen.filter((row) => inPeriod(row, from, to, source.kind));
      for (const row of (sample ? selected.slice(0, 2) : selected)) {
        signal.throwIfAborted();
        if (source.kind === "notice") {
          result.notices.push(row);
        } else {
          try {
            await pace();
            const card = await getPage(row.source_url, signal);
            const contract = readContract(card, row, source.kind);
            result.contracts.push({...contract, law: source.kind === "44" ? "44-ФЗ" : "223-ФЗ"});
            if (files) {
              for (const url of contract.attachment_tabs.slice(0, 1)) {
                await pace();
                const attachments = await getPage(url, signal);
                result.documents.push(...selectedLinks(attachments, /\/filestore\/.*\/download\//)
                  .map((source_url) => ({contract_id: row.id, source_url})));
              }
              if (source.kind === "223" && /щ[её]тк|спецтехник|трактор|снего|погрузчик|грейдер|отвал/i.test(contract.subject ?? "")) {
                for (const url of contract.object_links.slice(0, 1)) {
                  await pace();
                  const items = readSupplyLines(await getPage(url, signal));
                  result.supply_items.push(...items.map((item) => ({...item, contract_id: row.id, source_url: url})));
                }
              }
            }
          } catch (error) {
            if (signal.aborted) throw error;
            report.errors.push({id: row.id, url: row.source_url, message: String(error)});
            if (isAccessError(error)) throw error;
          }
        }
        report.in_period++;
      }
      if (sample) return;
      if (!(page.next > pageNumber)) {
        report.exhausted = page.expected === rows.size || (page.empty && rows.size === 0);
        if (!report.exhausted) {
          report.errors.push({url: listUrl.href, message: "Пагинация закончилась, точное число записей не подтверждено."});
        }
        return;
      }
      pageNumber = page.next;
    }
  }

  const panel = document.createElement("aside");
  panel.id = "kamchatka-eis";
  panel.style.cssText = "position:fixed;z-index:2147483647;bottom:18px;right:18px;width:350px;max-width:90vw;padding:16px;background:#fff;color:#172637;border:1px solid #194e61;border-radius:8px;box-shadow:0 6px 26px #16263740;font:14px/1.4 system-ui";
  panel.innerHTML = `
    <strong>Реестр зимнего содержания Камчатки</strong>
    <p><label>С <input type="date" data-key="from" value="2025-01-01"></label>
    <label>по <input type="date" data-key="to" value="2026-11-20"></label></p>
    <label><input type="checkbox" data-key="files"> Ссылки на вложения и позиции поставок</label>
    <p><button data-key="sample">Проба</button>
    <button data-key="run">Собрать</button>
    <button data-key="stop" disabled>Стоп</button></p>
    <button data-key="save" disabled>Сохранить JSON</button>
    <button data-key="close">Закрыть</button>
    <p data-key="status" role="status">Проба проверяет по одной странице каждого реестра.</p>`;
  document.body.append(panel);

  const ui = (key) => panel.querySelector(`[data-key="${key}"]`);
  let controller;
  let result;
  const progress = (message) => { ui("status").textContent = message; };

  async function run(sample) {
    if (controller) return;
    const from = ui("from").value;
    const to = ui("to").value;
    if (!from || !to || from > to) return progress("Укажите корректный период.");
    controller = new AbortController();
    result = {started_at: new Date().toISOString(), from, to, sample, complete: false,
      coverage: [], notices: [], contracts: [], contractors: [], documents: [], supply_items: []};
    for (const key of ["run", "sample", "close", "save"]) ui(key).disabled = true;
    ui("stop").disabled = false;
    try {
      for (const source of sources) {
        await collect(source, {sample, files: ui("files").checked, from, to}, result, controller.signal, progress);
      }
      result.complete = !sample && result.coverage.every((item) => item.exhausted && !item.errors.length);
      progress(`${sample ? "Проба" : result.complete ? "Сбор завершён" : "Частичный сбор"}: ${result.notices.length} извещений, ${result.contracts.length} контрактов. Скачайте JSON.`);
    } catch (error) {
      progress(controller.signal.aborted ? "Сбор остановлен. Можно сохранить результат." : String(error));
    } finally {
      const contractors = new Map();
      const noticeContracts = new Map();
      for (const contract of result.contracts) {
        if (contract.notice_id) {
          if (!noticeContracts.has(contract.notice_id)) noticeContracts.set(contract.notice_id, []);
          noticeContracts.get(contract.notice_id).push(contract.id);
        }
        for (const supplier of contract.suppliers) {
          if (!contractors.has(supplier.inn)) contractors.set(supplier.inn, {...supplier, contract_ids: []});
          contractors.get(supplier.inn).contract_ids.push(contract.id);
        }
      }
      result.contractors = [...contractors.values()];
      for (const notice of result.notices) notice.contract_ids = noticeContracts.get(notice.id) ?? [];
      result.finished_at = new Date().toISOString();
      controller = undefined;
      for (const key of ["run", "sample", "close", "save"]) ui(key).disabled = false;
      ui("stop").disabled = true;
    }
  }

  ui("sample").onclick = () => run(true);
  ui("run").onclick = () => run(false);
  ui("stop").onclick = () => controller?.abort();
  ui("close").onclick = () => panel.remove();
  ui("save").onclick = () => {
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type: "application/json"}));
    link.href = url;
    link.download = `kamchatka-eis-${result.from}-${result.to}${result.sample ? "-sample" : ""}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
})();
