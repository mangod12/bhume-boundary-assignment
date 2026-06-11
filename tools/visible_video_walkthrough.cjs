const path = require('path');
const { chromium } = require('playwright');

const root = process.cwd();
const base = 'https://hiring.bhume.in';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const uploads = [
  {
    label: 'Vadnerbhairav',
    file: path.join(root, 'data/outputs/final/vadner/predictions.geojson'),
    pauseMs: 20000,
  },
  {
    label: 'Malatavadi',
    file: path.join(root, 'data/outputs/final/malatavadi/predictions.geojson'),
    pauseMs: 20000,
  },
];

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 300 });
  const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await context.newPage();

  await page.goto('file:///' + path.join(root, 'video/teleprompter.html').replace(/\\/g, '/'));
  await sleep(6000);

  for (const route of ['/', '/understand/', '/task/', '/start/']) {
    await page.goto(base + route, { waitUntil: 'networkidle', timeout: 45000 });
    await sleep(6000);
  }

  for (const upload of uploads) {
    await page.goto(base + '/test/', { waitUntil: 'networkidle', timeout: 45000 });
    await page.getByRole('button', { name: upload.label }).click();
    await sleep(800);
    await page.locator('input[type=file]').first().setInputFiles(upload.file);
    await page.waitForFunction(() => /scored against|could not parse|invalid|error/i.test(document.body.innerText), null, { timeout: 30000 });
    await sleep(upload.pauseMs);
  }

  await page.goto('file:///' + path.join(root, 'README.md').replace(/\\/g, '/'));
  await sleep(9000);

  await page.goto(base + '/submit/', { waitUntil: 'networkidle', timeout: 45000 });
  await sleep(12000);

  await browser.close();
})();
