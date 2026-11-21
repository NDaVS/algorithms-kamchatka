import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { CameraMap } from './Map';
import { Icon, type IconName } from './Icon';
import type { ActionItem, Camera, Dashboard, EventItem, FeedbackVerdict, ObservedFrame, Scene, SignalReview } from './types';
import './styles.css';

const stateLabels: Record<string, string> = {
  new: 'Ожидает проверки', acknowledged: 'Принято', deferred: 'Отложено',
  en_route: 'Техника в пути', resolved: 'Закрыто',
};
const cameraLabels: Record<string, string> = {
  waiting: 'Ожидает кадр', available: 'Доступна', unchanged: 'Кадр без изменений',
  stale: 'Кадр устарел', error: 'Источник недоступен',
};
const deliveryLabels: Record<string, string> = {
  logged: 'В журнале', queued: 'В очереди', sending: 'Отправляется',
  sent: 'Принято почтовым сервером', ambiguous: 'Приём письма неизвестен', failed: 'Ошибка доставки',
};
const checkLabels: Record<string, string> = {
  ready: 'Работает', three_hourly: 'Прогноз на 5 суток · шаг 3 часа', mail_test: 'Тестовый приёмник Mailpit', unconfigured: 'Требует настройки', unavailable: 'Недоступен',
  invalid: 'Ответ вне схемы', fixtures: 'Контрольные данные',
  snow_only: 'Доступен признак падающего снега', v2_partial: 'Падающий снег проверен резервным анализом', local_log: 'Сообщения в локальном журнале',
};
const kindLabels: Record<string, string> = { snowfall: 'Снегопад', coverage: 'Снег на дороге', drift: 'Снежный вал' };
const transitions: Record<string, string[]> = {
  new: ['acknowledged', 'deferred', 'en_route', 'resolved'],
  acknowledged: ['deferred', 'en_route', 'resolved'],
  deferred: ['acknowledged', 'en_route', 'resolved'], en_route: ['deferred', 'resolved'], resolved: [],
};
const sections = [
  { id: 'overview', title: 'Обстановка', icon: 'overview' },
  { id: 'cameras', title: 'Камеры', icon: 'camera' },
  { id: 'forecast', title: 'Прогноз', icon: 'forecast' },
  { id: 'reports', title: 'Сводки и доставка', icon: 'reports' },
] as const satisfies ReadonlyArray<{ id: string; title: string; icon: IconName }>;
type Tab = typeof sections[number]['id'];
const currentTab = (): Tab => sections.find(section => '#' + section.id === location.hash)?.id ?? 'overview';
const params = new URLSearchParams(location.search);
const run = location.pathname === '/demo' ? 'demo' : params.get('run');
const suffix = run ? '?' + new URLSearchParams({ run }) : '';
const time = (value: string | null, seconds = false) => value ? new Intl.DateTimeFormat('ru', {
  dateStyle: 'short', timeStyle: seconds ? 'medium' : 'short', timeZone: 'Asia/Kamchatka',
}).format(new Date(value)) : '—';
const frameUrl = (frameId: string) => '/api/v1/frames/' + frameId + suffix;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1/' + path + suffix, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Запрос завершился с ошибкой (' + response.status + ')');
  }
  return response.json() as Promise<T>;
}

function SceneFacts({ scene, road }: { scene: Scene | null; road: boolean }) {
  const values: Record<string, string> = {
    yes: 'Да', no: 'Нет', uncertain: 'Не определено', none: 'Нет', light: 'Слабая',
    moderate: 'Умеренная', heavy: 'Сильная', good: 'Хорошая', reduced: 'Снижена',
    poor: 'Плохая', clear: 'Чисто', partial: 'Частично', mostly_covered: 'Большая часть',
    not_visible: 'Дорога вне кадра', limited: 'Обзор ограничен', unusable: 'Обзор недоступен', major: 'Сильное',
  };
  const valid = scene?.format_valid ? scene : null;
  const detail = valid?.details;
  const value = (field?: string) => field ? values[field] : 'Нет оценки';
  const visibility = valid?.visibility === 'uncertain' && detail
    ? detail.air_view === 'good' ? 'Хороший обзор' : value(detail.air_view)
    : value(valid?.visibility);
  const conclusion = !scene ? 'Ожидается анализ свежего кадра.'
    : !valid ? 'Анализ кадра не прошёл проверку.'
    : /\p{Script=Latin}/u.test(valid.evidence)
      ? { yes: 'В кадре виден снегопад.', no: 'Падающего снега не видно.',
          uncertain: 'По кадру не удалось определить, идёт ли снег.' }[valid.falling_snow]
      : valid.evidence;
  return <><dl className="facts">
    <div><dt>Падает снег</dt><dd>{value(valid?.falling_snow)}</dd></div>
    <div><dt>Интенсивность</dt><dd>{value(valid?.intensity)}</dd></div>
    <div><dt>Видимость по кадру</dt><dd>{visibility}</dd></div>
    <div><dt>Снег на дороге</dt><dd>{road ? value(valid?.road_coverage) : 'Вне дорожного мониторинга'}</dd></div>
    <div><dt>Снег на земле</dt><dd>{value(valid?.snow_on_ground)}</dd></div>
    <div><dt>Крупные валы</dt><dd>{value(valid?.large_snowdrifts)}</dd></div>
  </dl>
    <p className="evidence">{conclusion}</p>
    {valid?.snow_source === 'snow' && !detail && <p className="muted">Резервный анализ оценил только падающий снег.</p>}
    {detail && <details>
      <summary>Дополнительные признаки</summary>
      <dl className="facts">
        <div><dt>Позёмка</dt><dd>{value(detail.blowing_snow)}</dd></div>
        {road && <>
          <div><dt>Вал на проезжей части</dt><dd>{value(detail.snowdrift_on_road)}</dd></div>
          <div><dt>Снежная каша</dt><dd>{value(detail.slush_on_road)}</dd></div>
          <div><dt>Препятствие на дороге</dt><dd>{value(detail.road_obstruction)}</dd></div>
        </>}
      </dl>
    </details>}
  </>;
}

function CameraDetail({ camera, real }: { camera: Camera; real: boolean }) {
  return <section className="panel camera-detail" aria-label={'Оценка камеры: ' + camera.name}>
    <div className="camera-detail-media">
      {camera.frame_id ? <a className="full-frame" href={frameUrl(camera.frame_id)} target="_blank" rel="noreferrer"
        aria-label={'Открыть полный кадр: ' + camera.name}>
        <img src={frameUrl(camera.frame_id)} alt={camera.name} />
        <span><Icon name="expand" /> Полный кадр</span>
      </a> : <div className="image-empty"><Icon name="camera" /> Нет кадра</div>}
    </div>
    <div className="card-content">
      <CameraStatus camera={camera} />
      <h2>{camera.name}</h2>
      <p className="muted">{camera.road ? 'Наблюдение за дорогой' : 'Погодное наблюдение'}
        {camera.location_note && <><br />{camera.location_note}</>}
      </p>
      <dl className="camera-times">
        <div><dt>{real ? 'Кадр получен' : 'Время в демо'}</dt><dd>{time(camera.last_frame, true)}</dd></div>
        {real && <div><dt>Последняя проверка</dt><dd>{time(camera.last_poll, true)}</dd></div>}
        {real && camera.source_at && <div><dt>Обновление у источника</dt><dd>{time(camera.source_at, true)}</dd></div>}
      </dl>
      {real && camera.analyzed_frame_id && camera.analyzed_frame_id !== camera.frame_id &&
        <p className="muted">Новый кадр в обработке. Оценка относится к{' '}
          <a href={frameUrl(camera.analyzed_frame_id)} target="_blank" rel="noreferrer">
            кадру от {time(camera.analyzed_frame_at, true)}</a>.
        </p>}
      <SceneFacts scene={camera.scene} road={camera.road} />
    </div>
  </section>;
}

const verdictLabels: Record<FeedbackVerdict, string> = {
  confirmed: 'Подтвердилось', false_alarm: 'Ложный сигнал', already_working: 'Уже в работе',
};

function EvidenceFrame({ frame, label }: { frame: ObservedFrame; label: string }) {
  return <figure>
    <a href={frameUrl(frame.id)} target="_blank" rel="noreferrer"><img src={frameUrl(frame.id)} alt={label} /></a>
    <figcaption><strong>{label}</strong><br />{time(frame.observed_at, true)}</figcaption>
  </figure>;
}

function EventDetail({ event, role, close, refresh }: {
  event: EventItem; role: Dashboard['role']; close: () => void; refresh: () => Promise<void>;
}) {
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [review, setReview] = useState<SignalReview | null>(null);
  const [reviewError, setReviewError] = useState('');
  const [reviewBusy, setReviewBusy] = useState(false);
  const eventPath = 'events/' + event.id;
  const feedback = review?.feedback;
  const afterWork = review?.after;
  const [state, setState] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    let active = true;
    api<ActionItem[]>('events/' + event.id + '/actions').then(rows => { if (active) setActions(rows); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [event.id, event.version]);

  useEffect(() => {
    const controller = new AbortController();
    const refreshReview = () => api<SignalReview>(eventPath + '/review', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setReview(value); })
      .catch(e => { if (!controller.signal.aborted) setReviewError(e.message); });
    void refreshReview();
    const timer = ['en_route', 'resolved'].includes(event.state) && role === 'operator' && !reviewBusy
      ? window.setInterval(refreshReview, 12000) : undefined;
    return () => { controller.abort(); if (timer !== undefined) window.clearInterval(timer); };
  }, [eventPath, event.version, event.latest_frame, event.state, role, reviewBusy]);

  async function updateReview(path: string, body: object) {
    setReviewBusy(true);
    setReviewError('');
    try {
      await api(eventPath + path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      setReview(await api<SignalReview>(eventPath + '/review'));
      await refresh();
    } catch (e) {
      setReviewError((e as Error).message);
      try { setReview(await api<SignalReview>(eventPath + '/review')); } catch { /* Keep the original error. */ }
    } finally {
      setReviewBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('events/' + event.id + '/actions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: crypto.randomUUID(), expected_version: event.version, state, note }),
      });
      setState('');
      setNote('');
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel detail" aria-label="Карточка сигнала">
    <div className="detail-head">
      <div><EventSeverity event={event} />
      <h2 ref={heading} tabIndex={-1}>{event.name}</h2><p className="muted">{kindLabels[event.kind]} · {time(event.opened_at)}</p></div>
      <button className="quiet icon-button" onClick={close} aria-label="Закрыть карточку"><Icon name="close" /></button>
    </div>
    <div className="detail-body">
      <div className="evidence-images">
        <h3>Сравнение кадров</h3>
        <div className="frame-pair">
          {review?.before ? <EvidenceFrame frame={review.before} label="До ухудшения" />
            : <p className="frame-unavailable muted">{review ? 'Кадр до ухудшения не найден.' : 'Загружаю сравнение…'}</p>}
          <EvidenceFrame frame={review?.current ?? { id: event.latest_frame, observed_at: event.last_seen }}
            label="Последний подтверждающий кадр" />
        </div>
        {afterWork && <EvidenceFrame frame={afterWork} label="После работ · отмечено оператором" />}
        {role === 'operator' && review?.candidate && review.candidate.id !== afterWork?.id && <div className="after-work">
          <a href={frameUrl(review.candidate.id)} target="_blank" rel="noreferrer">
            Проверить кадр от {time(review.candidate.observed_at, true)}
          </a>
          <button className="quiet" disabled={reviewBusy}
            onClick={() => void updateReview('/after-work', { frame_id: review.candidate?.id })}>Закрепить как кадр после работ</button>
        </div>}
        {role === 'operator' && review && !review.candidate && !afterWork && ['en_route', 'resolved'].includes(event.state) &&
          <p className="muted">Ожидается новый кадр с этого ракурса для отметки после работ.</p>}
      </div>
      <div className="detail-controls">
        <p>{event.reason}</p>
        <p className="muted">{event.ended_at ? 'Признак завершился ' + time(event.ended_at) : (event.evidence_stale ? 'Нет свежего подтверждения · последний сигнал ' : 'Последнее подтверждение ') + time(event.last_seen)}</p>
        <h3>Решение · {stateLabels[event.state]}</h3>
        {role === 'operator' && transitions[event.state]?.length > 0 && <form onSubmit={submit} className="action-form">
          <label>Действие<select value={state} onChange={e => setState(e.target.value)} required>
            <option value="">Выбрать действие</option>
            {transitions[event.state].map(option => <option key={option} value={option}>{stateLabels[option]}</option>)}
          </select></label>
          <label>Комментарий<input value={note} maxLength={500} onChange={e => setNote(e.target.value)}
            required={state === 'deferred' || state === 'resolved'} /></label>
          <button disabled={busy || !state}>{busy ? 'Сохраняю…' : 'Сохранить'}</button>
        </form>}
        {error && <p role="alert" className="error">{error}</p>}
        <div className="feedback-panel">
          <h3>Оценка сигнала</h3>
          {role === 'operator' && <div className="feedback-buttons">
            {(Object.keys(verdictLabels) as FeedbackVerdict[]).map(verdict =>
              <button className="quiet" key={verdict} disabled={reviewBusy || !review}
                aria-pressed={feedback?.verdict === verdict}
                onClick={() => void updateReview('/feedback', { verdict })}>{verdictLabels[verdict]}</button>)}
          </div>}
          {feedback && <p className="muted" role="status">Записано: {verdictLabels[feedback.verdict]}</p>}
          {reviewError && <p className="error" role="alert">{reviewError}</p>}
        </div>
        {actions.length > 0 && <ul className="timeline">{actions.map(action => <li key={action.request_id}>
          <strong>{stateLabels[action.state]}</strong> · {time(action.created_at)} · {action.actor}
          {action.note && <p>{action.note}</p>}
        </li>)}</ul>}
      </div>
    </div>
  </section>;
}

function cameraHealthy(camera: Camera) {
  return camera.frame_id && (camera.status === 'available' || camera.status === 'unchanged');
}

function CameraStatus({ camera }: { camera: Camera }) {
  const healthy = cameraHealthy(camera);
  const degraded = camera.status === 'error' || camera.status === 'stale';
  return <span className={'status ' + (healthy ? 'ok' : degraded ? 'watch' : 'neutral')}>
    <span className="indicator" />{cameraLabels[camera.status] ?? camera.status}
  </span>;
}

function cameraName(camera: Camera) {
  if (camera.id === 'moroznaya') return 'Гора Морозная';
  if (camera.id === 'ran-avachinsky') return 'Мишенная сопка';
  return camera.name.replace(/^(Петропавловск-Камчатский|Елизово), /, '');
}

function CameraStrip({ cameras, selected, onSelect, real }: {
  cameras: Camera[]; selected?: string; onSelect: (id: string) => void; real: boolean;
}) {
  return cameras.length ? <div className="camera-strip">{cameras.map(camera =>
    <button className="camera-tile" type="button" key={camera.id} onClick={() => onSelect(camera.id)}
      title={camera.name} aria-pressed={selected === undefined ? undefined : selected === camera.id}>
      <div className="tile-image">
        {camera.frame_id ? <img src={frameUrl(camera.frame_id)} alt="" loading="lazy" />
          : <div className="image-empty"><Icon name="camera" /> Нет кадра</div>}
        <span className="tile-type">{camera.road ? 'Дорога' : 'Погода'}</span>
      </div>
      <div className="tile-info">
        <strong>{cameraName(camera)}</strong>
        <small>{camera.area}</small>
        <div className="tile-foot">
          <CameraStatus camera={camera} />
          {camera.last_frame && <span className="tile-time" title={time(camera.last_frame, true)}>
            {real ? timeOfDay(camera.last_frame) : time(camera.last_frame)}
          </span>}
        </div>
      </div>
    </button>)}</div> : <Empty icon="camera">Камеры появятся после загрузки данных.</Empty>;
}

function EventSeverity({ event }: { event: EventItem }) {
  return event.ended_at || event.evidence_stale
    ? <span className="status neutral">{event.ended_at ? 'Завершён' : 'Нет свежего подтверждения'}</span>
    : <span className={'status ' + event.severity}><span className="indicator" />
        {event.severity === 'urgent' ? 'Срочно' : 'Наблюдение'}
      </span>;
}

function Empty({ icon, children, hint }: { icon: IconName; children: ReactNode; hint?: string }) {
  return <div className="empty"><Icon name={icon} /><strong>{children}</strong>{hint && <p>{hint}</p>}</div>;
}

const kamchatkaClock = new Intl.DateTimeFormat('ru', {
  timeZone: 'Asia/Kamchatka', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});
const timeOfDay = (value: string) => kamchatkaClock.format(new Date(value));
const modeLabels = { demo: 'Демо', replay: 'Воспроизведение', preview: 'Реальные данные', live: 'Рабочий режим' };
const deliveryKinds: Record<string, string> = { event: 'Сигнал', forecast: 'Прогноз', summary: 'Сводка' };

function forecastChecks(data: Dashboard) {
  const points = data.checks.filter(check => check.name.startsWith('forecast:'));
  return points.length ? points : data.checks.filter(check => check.name === 'forecast');
}

function ForecastPage({ data, real }: { data: Dashboard; real: boolean }) {
  const checks = forecastChecks(data);
  return <div className="page-stack">
    {data.forecasts.length ? data.forecasts.map(forecast => {
      const threeHourly = forecast.periods.some(period => period.horizon === 'three_hourly');
      const relevant = forecast.periods.filter(period => period.horizon === 'daily' || period.snow);
      const shown = threeHourly ? forecast.periods : relevant.length ? relevant : forecast.periods.slice(0, 12);
      return <article className="panel forecast" key={forecast.point}>
        <div className="panel-head"><div><h2>{forecast.point}</h2>
          <p>{real ? 'OpenWeather' : 'Контрольный прогноз'}{threeHourly && ' · 5 суток, шаг 3 часа'} · {time(forecast.fetched_at)}</p></div></div>
        {shown.length ? <div className="table-scroll" tabIndex={0} aria-label={'Таблица прогноза: ' + forecast.point}>
          <table><thead><tr><th>Период</th><th>Вероятность осадков</th><th>Снег, мм</th>
            <th>Воздух, °C</th><th>Ветер, м/с</th></tr></thead>
            <tbody>{shown.map(period =>
              <tr key={period.at + period.horizon}><td>{time(period.at)} · {{ daily: 'сутки', hourly: 'час', three_hourly: '3 часа' }[period.horizon]}</td>
                <td>{period.probability == null ? '—' : Math.round(period.probability * 100) + '%'}</td>
                <td>{period.snow_mm ?? '—'}</td><td>{period.temperature_c ?? '—'}</td><td>{period.wind_mps ?? '—'}</td></tr>)}</tbody>
          </table>
        </div> : <Empty icon="forecast">Периоды прогноза отсутствуют</Empty>}
      </article>;
    }) : <section className="panel"><Empty icon="forecast" hint="Здесь появятся ожидаемые осадки, температура и ветер.">
      Прогноз ещё не получен
    </Empty></section>}
    {checks.length > 0 && <section className="panel">
      <div className="panel-head"><h2>Источник прогноза</h2></div>
      <dl className="health-list">{checks.map(check =>
        <div key={check.name}><dt>{check.name.split(':')[1] ?? 'OpenWeather'}</dt>
          <dd>{checkLabels[check.status] ?? check.status}</dd><dd className="muted">{time(check.observed_at)}</dd>
        </div>)}</dl>
    </section>}
  </div>;
}

function ReportsPage({ data }: { data: Dashboard }) {
  const mailTest = data.checks.some(check => check.name === 'mail' && check.status === 'mail_test');
  return <div className="reports-grid">
    <section className="panel">
      <div className="panel-head"><div><h2>{data.mode === 'demo' || data.mode === 'replay' ? 'Сводки прогона' : 'Утренние сводки'}</h2>
        <p>Обстановка и действия за период</p></div></div>
      <dl className="feedback-totals">{(Object.keys(verdictLabels) as FeedbackVerdict[]).map(verdict =>
        <div key={verdict}><dt>{verdictLabels[verdict]}</dt><dd>{data.feedback_counts[verdict] ?? 0}</dd></div>)}</dl>
      {data.summaries.length ? data.summaries.map(summary => <details className="journal-entry" key={summary.day}>
        <summary><span>{time(summary.payload.end)}<small>Сигналов: {summary.payload.events.length}</small></span></summary>
        <div className="journal-body">
          <p className="muted">{time(summary.payload.start)} — {time(summary.payload.end)}</p>
          {summary.payload.events.map(item => <p key={item.id}>{item.name} · {stateLabels[item.state]} · {item.reason}</p>)}
          {summary.payload.stale_cameras.length > 0 && <p>Нет свежих кадров: {summary.payload.stale_cameras.join(', ')}</p>}
          {summary.payload.actions?.length > 0 && <ul>{summary.payload.actions.map((action, index) =>
            <li key={action.name + action.created_at + index}>{action.name} · {stateLabels[action.state]} · {time(action.created_at)}
              {action.note && <span> · {action.note}</span>}</li>)}</ul>}
        </div>
      </details>) : <Empty icon="reports">Сводок пока нет</Empty>}
    </section>
    <section className="panel">
      <div className="panel-head"><div><h2>Журнал доставки</h2><p>Оповещения и состояние отправки</p></div></div>
      {data.deliveries.length ? data.deliveries.map(delivery => <details className="journal-entry" key={delivery.id}>
        <summary><span>{delivery.subject}<small>{deliveryKinds[delivery.kind] ?? delivery.kind} · {time(delivery.created_at)}</small></span>
          <span className={'status ' + (delivery.stage === 'failed' ? 'urgent' : 'neutral')}>{deliveryLabels[delivery.stage] ?? delivery.stage}</span></summary>
        <div className="journal-body">
          <p className="muted">Попыток: {delivery.attempts}</p>
          <p className="message">{delivery.body}</p>
          {delivery.error && <p className="error">{delivery.error}</p>}
        </div>
      </details>) : <Empty icon="bell">Сообщений пока нет</Empty>}
    </section>
  </div>;
}

function App() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [tab, setTab] = useState<Tab>(currentTab);
  const [cameraId, setCameraId] = useState<string>();
  const [eventId, setEventId] = useState(params.get('event'));
  const [error, setError] = useState('');
  const [refreshAt, setRefreshAt] = useState('');
  const [onlyActive, setOnlyActive] = useState(true);
  const [linkedEvent, setLinkedEvent] = useState<EventItem | null>(null);
  const [linkError, setLinkError] = useState('');
  const requestInFlight = useRef<Promise<void> | null>(null);
  const eventTrigger = useRef<HTMLButtonElement | null>(null);
  const cameraPanel = useRef<HTMLDivElement>(null);
  const refresh = () => {
    if (requestInFlight.current) return requestInFlight.current;
    requestInFlight.current = api<Dashboard>('dashboard').then(result => {
      setData(result); setError(''); setRefreshAt(new Date().toISOString());
    }).catch(e => { setError(e.message); }).finally(() => { requestInFlight.current = null; });
    return requestInFlight.current;
  };

  useEffect(() => {
    const sync = () => setTab(currentTab());
    window.addEventListener('hashchange', sync);
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { window.removeEventListener('hashchange', sync); window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (tab === 'cameras' && cameraId) cameraPanel.current?.focus({ preventScroll: true });
  }, [tab, cameraId]);

  const real = data?.mode === 'preview' || data?.mode === 'live';
  const listedEvent = data?.events.find(item => item.id === eventId);
  useEffect(() => {
    setLinkError('');
    if (!eventId || listedEvent) return;
    let active = true;
    api<EventItem>('events/' + encodeURIComponent(eventId)).then(item => { if (active) setLinkedEvent(item); })
      .catch(e => { if (active) setLinkError(e.message); });
    return () => { active = false; };
  }, [eventId, Boolean(listedEvent)]);
  const event = listedEvent ?? (linkedEvent?.id === eventId ? linkedEvent : null);
  const active = data?.events.filter(item => item.state !== 'resolved') ?? [];
  const events = onlyActive ? active : data?.events ?? [];
  const urgent = active.filter(item => item.severity === 'urgent' && !item.ended_at && !item.evidence_stale).length;
  const cameras = [...(data?.cameras ?? [])].sort((first, second) => Number(second.road) - Number(first.road));
  const healthy = cameras.filter(cameraHealthy);
  const selectedCamera = cameras.find(camera => camera.id === cameraId) ?? cameras[0];
  const weatherAvailable = Boolean(data?.forecasts.length);
  const forecastUnavailable = data && !weatherAvailable && forecastChecks(data).some(check => check.status === 'unavailable');
  const heroDemo = !real && data?.mode === 'demo' && (active[0]?.latest_frame ?? cameras.find(camera => camera.frame_id)?.frame_id);
  const page = {
    overview: ['Дорожная обстановка', 'Текущие сигналы и состояние камер'],
    cameras: ['Камеры', 'Последние кадры и оценки по каждому ракурсу'],
    forecast: ['Прогноз', 'Ожидаемые осадки, температура и ветер'],
    reports: ['Сводки и доставка', 'Журнал погодных предупреждений и действий'],
  }[tab];

  function openCamera(id: string) {
    setCameraId(id);
    if (tab !== 'cameras') {
      location.hash = 'cameras';
      window.scrollTo(0, 0);
    } else {
      cameraPanel.current?.scrollIntoView({ block: 'nearest' });
    }
  }

  function closeEvent() {
    setEventId(null);
    eventTrigger.current?.focus();
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={e => {
      e.preventDefault();
      document.getElementById('main-content')?.focus();
    }}>К основному содержанию</a>
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="Алгоритмы Камчатки, обстановка">
        <span className="brand-mark"><Icon name="mark" width="23" height="23" /></span>
        <span><strong>Алгоритмы<br />{' '}Камчатки</strong><small>Зимний мониторинг</small></span>
      </a>
      <nav aria-label="Разделы">{sections.map(section =>
        <a key={section.id} href={'#' + section.id} aria-current={section.id === tab ? 'page' : undefined}
          onClick={() => { if (section.id !== tab) window.scrollTo(0, 0); }}>
          <Icon name={section.icon} /><span>{section.title}</span></a>
      )}</nav>
      <div className="sidebar-bottom">
        {data && <span className={'mode ' + (real ? 'mode-real' : 'mode-demo')}><span className="indicator" />{modeLabels[data.mode]}</span>}
        {data?.mode === 'preview' && <a className="mode-link" href="/demo">Открыть демо <Icon name="chevron" width="16" height="16" /></a>}
        {data?.mode === 'demo' && location.pathname === '/demo' &&
          <a className="mode-link" href="/">Реальные данные <Icon name="chevron" width="16" height="16" /></a>}
      </div>
    </aside>
    <main className="workspace" id="main-content" tabIndex={-1}>
      <header className="workspace-head">
        <div><h1>{page[0]}</h1><p>{page[1]}</p></div>
        <div className="page-tools">
          <div className="refresh-time"><span>Камчатка · UTC+12</span>
            <strong>{refreshAt ? 'Обновлено ' + timeOfDay(refreshAt) : 'Ожидается обновление'}</strong>
          </div>
          <button className="quiet icon-button" onClick={refresh} title="Обновить данные" aria-label="Обновить данные"><Icon name="refresh" /></button>
        </div>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {!data && !error && <p className="muted" role="status">Загружаю данные мониторинга…</p>}
      {data && !real && <p className="demo-note">{data.mode === 'demo'
        ? 'Демонстрационный сценарий. Кадры и события взяты из контрольного прогона.'
        : 'Воспроизведение сохранённых наблюдений.'}</p>}
      {data && tab === 'overview' && <>
        <dl className="stats">
          <div><dt>Камеры со свежим кадром</dt><dd>{healthy.length}<span> / {cameras.length}</span></dd></div>
          <div><dt>Открытые сигналы</dt><dd>{active.length}</dd></div>
          <div><dt>Срочно проверить</dt><dd className={urgent ? 'hot' : ''}>{urgent}</dd></div>
          <div><dt>По прогнозу</dt><dd>{data.warnings.length ? <>{data.warnings.length}<span> предупр.</span></>
            : <span className="metric-text">{forecastUnavailable ? 'Недоступен' : weatherAvailable ? 'Без предупреждений' : 'Нет данных'}</span>}</dd></div>
        </dl>
        {linkError && <p className="error" role="alert">{linkError}</p>}
        {event && <EventDetail key={event.id} event={event} role={data.role} close={closeEvent} refresh={refresh} />}
        <div className="overview-grid">
          <section className="panel territory">
            <div className="panel-head"><div><h2>{heroDemo ? 'Контрольный кадр' : 'Карта наблюдений'}</h2>
              <p>{heroDemo ? 'Пример из демонстрационного прогона' : 'Петропавловск-Камчатский и Елизово'}</p></div>
              {!heroDemo && <Icon name="map" className="muted" />}
            </div>
            {heroDemo ? <figure className="demo-frame"><img src={frameUrl(heroDemo)} alt="Контрольный кадр сильного снегопада" /></figure>
              : <CameraMap cameras={cameras} onSelect={openCamera} />}
            {!heroDemo && cameras.some(camera => camera.coordinates) && <div className="map-legend">
              <span><i className="legend-road" />Дорожные</span>
              <span><i className="legend-weather" />Погодные</span>
            </div>}
          </section>
          <section className="panel signal-panel">
            <div className="panel-head"><h2>Сигналы</h2>
              <label className="toggle"><input type="checkbox" checked={onlyActive} onChange={e => setOnlyActive(e.target.checked)} /> Только открытые</label></div>
            {events.length ? <div className="event-list">{events.map(item =>
              <button className="event-row" key={item.id} onClick={e => { eventTrigger.current = e.currentTarget; setEventId(item.id); }}
                aria-expanded={eventId === item.id}>
                <span className="event-meta"><EventSeverity event={item} /><small>{time(item.last_seen)}</small></span>
                <strong>{item.name}</strong><span>{item.reason}</span>
                <small className="event-state">{stateLabels[item.state]} <Icon name="chevron" width="16" height="16" /></small>
              </button>)}</div>
              : <Empty icon="check">{onlyActive ? 'Открытых сигналов нет' : 'Событий пока нет'}</Empty>}
            <div className="signal-forecast">
              <div><Icon name="forecast" /><h3>Предупреждения по прогнозу</h3></div>
              {data.warnings.length ? data.warnings.map(warning => <p key={warning.point}>
                <strong>{warning.point}</strong><small>{time(warning.payload.start)} — {time(warning.payload.end)}</small></p>)
                : <p className="muted">{forecastUnavailable ? 'Источник прогноза недоступен.' : weatherAvailable ? 'Действующих предупреждений нет.' : 'Прогноз ещё не получен.'}</p>}
              <a href="#forecast">Смотреть прогноз</a>
            </div>
          </section>
        </div>
        <section className="panel inspection-panel">
          <div className="panel-head"><div><h2>Очередь проверки участков</h2><p>Срочность, важность участка и свежесть кадра</p></div></div>
          {data.inspection_queue.length ? <ol className="inspection-list">{data.inspection_queue.map(item =>
            <li key={item.event_id}><button className="inspection-row" onClick={e => {
              eventTrigger.current = e.currentTarget; setEventId(item.event_id);
            }}>
              <span><strong>{item.name}</strong><small>{item.factors.join(' · ')}</small></span>
              <span className={'status ' + item.severity}>{item.severity === 'urgent' ? 'Срочно' : 'Проверить'}</span>
            </button></li>)}</ol> : <p className="inspection-empty muted">Нет дорожных сигналов, требующих проверки.</p>}
        </section>
        <section className="camera-section">
          <div className="section-heading"><h2>Камеры</h2><a href="#cameras">Все камеры</a></div>
          <CameraStrip cameras={cameras} onSelect={openCamera} real={real} />
        </section>
      </>}
      {data && tab === 'cameras' && <div className="page-stack">
        <CameraStrip cameras={cameras} selected={selectedCamera?.id} onSelect={openCamera} real={real} />
        {selectedCamera && <div ref={cameraPanel} tabIndex={-1} className="camera-selection">
          <CameraDetail camera={selectedCamera} real={real} />
        </div>}
        <section className="panel">
          <div className="panel-head"><div><h2>Обработка кадров</h2><p>В очереди: {data.queue.pending}
            {data.queue.oldest && ' · старейший кадр ' + time(data.queue.oldest)}</p></div></div>
          <dl className="health-list">{data.checks.filter(check => !check.name.startsWith('forecast')).map(check =>
            <div key={check.name}><dt>{{ model: 'Модель', worker: 'Обработчик', mail: 'Почта' }[check.name] ?? check.name}</dt>
              <dd>{checkLabels[check.status] ?? check.status}</dd><dd className="muted">{time(check.observed_at)}</dd></div>
          )}</dl>
        </section>
      </div>}
      {data && tab === 'forecast' && <ForecastPage data={data} real={real} />}
      {data && tab === 'reports' && <ReportsPage data={data} />}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
