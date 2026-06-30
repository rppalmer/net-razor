import { BackendError } from './errors.js';

// SearchTimeline only. These are fallback candidates; runtime discovery is attempted when X
// rotates them. No operation IDs for mutations or unrelated timelines are present.
export const SEARCH_TIMELINE_QUERY_IDS = [
  '6AAys3t42mosm_yTI_QENg',
  'M1jEez78PEfVfbQLvlWMvQ',
  '5h0kNbk3ii97rmfY6CdgAA',
  'Tp1sewRU1AsZpBWhqCZicQ',
];

const DISCOVERY_PAGES = [
  'https://x.com/?lang=en',
  'https://x.com/explore',
];
const BUNDLE_URL_PATTERN =
  /https:\/\/abs\.twimg\.com\/responsive-web\/client-web(?:-legacy)?\/[A-Za-z0-9.-]+\.js/g;
const OPERATION_PATTERNS = [
  /queryId\s*:\s*["']([A-Za-z0-9_-]+)["']\s*,\s*operationName\s*:\s*["']SearchTimeline["']/g,
  /operationName\s*:\s*["']SearchTimeline["']\s*,\s*queryId\s*:\s*["']([A-Za-z0-9_-]+)["']/g,
];
const DISCOVERY_HEADERS = {
  'user-agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    + 'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  accept: 'text/html,application/javascript;q=0.9,*/*;q=0.8',
  'accept-language': 'en-US,en;q=0.9',
};

async function fetchText(url, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: DISCOVERY_HEADERS,
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new BackendError('request_failed', {
        retryable: response.status >= 500,
        statusCode: response.status,
      });
    }
    return await response.text();
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new BackendError('timeout', { retryable: true });
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function discoverBundleUrls(timeoutMs) {
  const urls = new Set();
  for (const pageUrl of DISCOVERY_PAGES) {
    try {
      const html = await fetchText(pageUrl, timeoutMs);
      for (const match of html.matchAll(BUNDLE_URL_PATTERN)) {
        urls.add(match[0]);
      }
    } catch {
      // Another discovery page may still work.
    }
  }
  return [...urls];
}

function extractSearchTimelineId(source) {
  for (const pattern of OPERATION_PATTERNS) {
    pattern.lastIndex = 0;
    const match = pattern.exec(source);
    if (match?.[1]) {
      return match[1];
    }
  }
  return undefined;
}

export async function discoverSearchTimelineQueryId(timeoutMs) {
  const bundleUrls = await discoverBundleUrls(timeoutMs);
  const concurrency = 6;

  for (let index = 0; index < bundleUrls.length; index += concurrency) {
    const chunk = bundleUrls.slice(index, index + concurrency);
    const matches = await Promise.all(
      chunk.map(async (url) => {
        try {
          const source = await fetchText(url, timeoutMs);
          return extractSearchTimelineId(source);
        } catch {
          return undefined;
        }
      }),
    );
    const queryId = matches.find(Boolean);
    if (queryId) {
      return queryId;
    }
  }
  return undefined;
}
