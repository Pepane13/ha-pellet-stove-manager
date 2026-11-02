// Pellet Stove Manager (Homey) -> Home Assistant: export of the learnt pellets / cleaning state.
// Read-only. Paste in HomeyScript, run, copy the returned JSON to Claude
// (it feeds the action pellet_stove_manager.import_homey). No secret is exported.
const APP = 'com.pepane13.pelletstove';
const KEYS = ['consumables', 'consumables_settings'];

async function setting(name) {
  const tries = [
    () => Homey.apps.getAppSetting({ id: APP, name }),
    async () => (await Homey.apps.getAppSettings({ id: APP }))[name],
  ];
  for (const t of tries) {
    try { const v = await t(); if (v !== undefined) return v; } catch (e) { /* next */ }
  }
  return null;
}

const out = {};
for (const k of KEYS) out[k] = await setting(k);
if (!out.consumables) throw new Error('App setting « consumables » not readable from HomeyScript');
const c = out.consumables;
// keep what Home Assistant uses, drop the daily learning detail
const slim = {
  pellets: c.pellets, cleaning: c.cleaning, maintenance: { lastDate: c.maintenance && c.maintenance.lastDate },
  months: c.months, last: c.last, settings: out.consumables_settings || {},
};
const text = JSON.stringify(slim);
log(`kg burnt since refill: ${c.pellets && c.pellets.kgBurned}, factor: ${c.pellets && c.pellets.factor && c.pellets.factor.value}, cleaning h: ${c.cleaning && c.cleaning.hours}, refills: ${(c.pellets && c.pellets.refills || []).length}`);
return text;
