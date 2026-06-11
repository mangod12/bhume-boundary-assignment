import { chromium } from 'playwright';

const routes = ['/', '/understand/', '/playground/', '/task/', '/start/', '/test/', '/submit/'];
const base = process.env.BHUME_BASE_URL || 'https://hiring.bhume.in';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

const output = [];
for (const path of routes) {
  const url = `${base}${path}`;
  const response = await page.goto(url, { waitUntil: 'domcontentloaded' });
  const data = await page.evaluate(() => {
    const body = document.body ? document.body.innerText : '';
    const headings = Array.from(document.querySelectorAll('h1,h2,h3')).map((el) => el.textContent?.trim()).filter(Boolean);
    const links = Array.from(document.querySelectorAll('a[href]')).map((el) => ({
      text: (el.textContent || '').trim().replace(/\s+/g, ' '),
      href: el.getAttribute('href') || '',
    }));
    const relevantLinks = links.filter((x) => x.href);
    const navCandidates = links.filter((x) =>
      x.href.startsWith('/') ||
      x.href.includes('hiring.bhume.in') ||
      x.href.includes('docs.google.com') ||
      x.href.includes('forms.gle')
    );
    const constraints = {
      hasPlotNumber: /plot_number/i.test(body),
      hasCorrected: /corrected/i.test(body),
      hasFlagged: /flagged/i.test(body),
      hasConfidence: /confidence/i.test(body),
      hasMethodNote: /method_note/i.test(body),
      hasPredictionsLink: /predictions\.geojson/i.test(body),
      hasSubmissionForm: relevantLinks.some((x) => /docs\.google\.com\/forms/i.test(x.href) || /forms\.gle/i.test(x.href)),
    };
    return {
      headings,
      navLinks: navCandidates,
      links: relevantLinks.map((x) => ({ text: x.text, href: x.href })),
      constraints,
    };
  });

  output.push({
    route: path,
    status: response?.status() ?? null,
    title: await page.title(),
    headingCount: data.headings.length,
    headings: data.headings,
    navLinks: data.navLinks,
    links: data.links,
    pageActions: {
      hasGoogleForm: data.links.some((x) => /docs\.google\.com\/forms/i.test(x.href) || /forms\.gle/i.test(x.href)),
      hasPredictionsHint: data.links.some((x) => /predictions\.geojson/i.test(x.text + ' ' + x.href)),
      hasExternalDownload: data.links.some((x) => /^https?:\/\//i.test(x.href)),
    },
    constraints: data.constraints,
  });
}

await browser.close();

console.log(JSON.stringify({
  timestamp_utc: new Date().toISOString(),
  base,
  routesChecked: output.length,
  allOk: output.every((item) => item.status === 200),
  pages: output,
}, null, 2));
