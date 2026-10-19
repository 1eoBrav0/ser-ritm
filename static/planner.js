/* Shared pure calculations: the charts use real intervals, including overlaps. */
(function (root) {
  function summarize(entries, key) {
    const items = entries.filter(e => e.day === key && e.kind !== 'note');
    const midnight = new Date(`${key}T00:00:00`);
    const tomorrow = new Date(midnight);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const length = (tomorrow - midnight) / 60000;
    const intervals = items.filter(e => e.starts_at && e.ends_at).map(e => ({
      entry: e,
      start: Math.max(0, (new Date(e.starts_at) - midnight) / 60000),
      end: Math.min(length, (new Date(e.ends_at) - midnight) / 60000)
    })).filter(i => Number.isFinite(i.start) && Number.isFinite(i.end) && i.end > i.start);
    const points = [];
    intervals.forEach(i => { points.push([i.start, 1], [i.end, -1]); });
    points.sort((a, b) => a[0] - b[0]);
    let busy = 0, overlap = 0, active = 0, last = 0;
    for (const [minute, delta] of points) {
      if (active > 0) busy += minute - last;
      if (active > 1) overlap += minute - last;
      active += delta;
      last = minute;
    }
    return {items, intervals, length, busy, overlap,
      done: items.filter(e => e.done).length,
      important: items.filter(e => !e.done && e.priority === 'high').length,
      untimed: items.filter(e => !e.starts_at || !e.ends_at).length};
  }
  const minutesText = minutes => {
    minutes = Math.round(minutes);
    return [Math.floor(minutes / 60) ? `${Math.floor(minutes / 60)} ч` : '',
      minutes % 60 ? `${minutes % 60} мин` : ''].filter(Boolean).join(' ') || '0 мин';
  };
  const planner = {summarize, minutesText};
  if (typeof module !== 'undefined' && module.exports) module.exports = planner;
  else root.Planner = planner;
})(globalThis);
