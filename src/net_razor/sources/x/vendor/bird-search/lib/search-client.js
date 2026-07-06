import { randomBytes, randomUUID } from 'node:crypto';

import { BackendError } from './errors.js';
import { discoverSearchTimelineQueryId, SEARCH_TIMELINE_QUERY_IDS } from './query-ids.js';
import { buildSearchFeatures } from './search-features.js';
import { extractBottomCursor, parseTweetsFromInstructions } from './search-parser.js';

const GRAPHQL_BASE = 'https://x.com/i/api/graphql';
const WEB_BEARER_TOKEN =
  'AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D'
  + '1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA';
const QUERY_ID_MISMATCH_PATTERN =
  /GRAPHQL_VALIDATION_FAILED|rawQuery.{0,120}must be defined/i;
const AUTH_FAILURE_PATTERN = /auth|credential|login|unauthorized|forbidden/i;
const RATE_LIMIT_PATTERN = /rate.?limit|too many requests/i;

function looksLikeHtml(text, contentType) {
  const trimmed = text.trimStart().toLowerCase();
  return contentType.includes('text/html')
    || trimmed.startsWith('<!doctype')
    || trimmed.startsWith('<html');
}

function graphqlErrors(data) {
  return Array.isArray(data?.errors) ? data.errors : [];
}

function errorMessages(errors) {
  return errors
    .map((error) => (typeof error?.message === 'string' ? error.message : ''))
    .join(' ');
}

function isQueryIdMismatch(status, text, data) {
  if (![400, 404, 422].includes(status)) {
    return false;
  }
  if (QUERY_ID_MISMATCH_PATTERN.test(text)) {
    return true;
  }
  return graphqlErrors(data).some(
    (error) => error?.extensions?.code === 'GRAPHQL_VALIDATION_FAILED',
  );
}

function classifyHttpFailure(status, text, contentType) {
  if (looksLikeHtml(text, contentType)) {
    return new BackendError('blocked', { retryable: true, statusCode: status });
  }
  if (status === 401) {
    return new BackendError('auth_failed', { statusCode: status });
  }
  if (status === 403) {
    return new BackendError('blocked', { statusCode: status });
  }
  if (status === 429) {
    return new BackendError('rate_limited', { retryable: true, statusCode: status });
  }
  if (status >= 500) {
    return new BackendError('request_failed', { retryable: true, statusCode: status });
  }
  return new BackendError('request_failed', { statusCode: status });
}

function classifyGraphqlFailure(errors) {
  const messages = errorMessages(errors);
  const codes = new Set(errors.map((error) => error?.extensions?.code));
  if (codes.has(88) || RATE_LIMIT_PATTERN.test(messages)) {
    return new BackendError('rate_limited', { retryable: true });
  }
  if (codes.has(32) || codes.has(89) || AUTH_FAILURE_PATTERN.test(messages)) {
    return new BackendError('auth_failed');
  }
  if (codes.has('GRAPHQL_VALIDATION_FAILED')) {
    return new BackendError('upstream_changed', { queryIdFailure: true });
  }
  return new BackendError('request_failed');
}

export class SearchClient {
  constructor(options) {
    this.authToken = options.authToken;
    this.ct0 = options.ct0;
    this.timeoutMs = options.timeoutMs;
    this.clientUuid = randomUUID();
    this.clientDeviceId = randomUUID();
    this.discoveredQueryId = undefined;
  }

  headers() {
    return {
      accept: '*/*',
      'accept-language': 'en-US,en;q=0.9',
      authorization: `Bearer ${WEB_BEARER_TOKEN}`,
      'content-type': 'application/json',
      cookie: `auth_token=${this.authToken}; ct0=${this.ct0}`,
      origin: 'https://x.com',
      referer: 'https://x.com/',
      'user-agent':
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        + 'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      'x-client-transaction-id': randomBytes(16).toString('hex'),
      'x-client-uuid': this.clientUuid,
      'x-csrf-token': this.ct0,
      'x-twitter-active-user': 'yes',
      'x-twitter-auth-type': 'OAuth2Session',
      'x-twitter-client-deviceid': this.clientDeviceId,
      'x-twitter-client-language': 'en',
    };
  }

  async fetchWithTimeout(url, options) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new BackendError('timeout', { retryable: true });
      }
      throw new BackendError('request_failed', { retryable: true });
    } finally {
      clearTimeout(timeout);
    }
  }

  queryIds() {
    return [
      ...(this.discoveredQueryId ? [this.discoveredQueryId] : []),
      ...SEARCH_TIMELINE_QUERY_IDS,
    ].filter((value, index, values) => values.indexOf(value) === index);
  }

  async fetchPage(query, count, cursor, product) {
    let onlyQueryIdFailures = true;

    for (const queryId of this.queryIds()) {
      const variables = {
        rawQuery: query,
        count,
        querySource: 'typed_query',
        product,
        ...(cursor ? { cursor } : {}),
      };
      const params = new URLSearchParams({ variables: JSON.stringify(variables) });
      const url = `${GRAPHQL_BASE}/${queryId}/SearchTimeline?${params.toString()}`;
      const response = await this.fetchWithTimeout(url, {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({
          features: buildSearchFeatures(),
          queryId,
        }),
      });
      const contentType = response.headers.get('content-type')?.toLowerCase() ?? '';
      const text = await response.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch {
        data = undefined;
      }

      if (isQueryIdMismatch(response.status, text, data)) {
        continue;
      }
      onlyQueryIdFailures = false;
      if (!response.ok) {
        throw classifyHttpFailure(response.status, text, contentType);
      }
      if (data === undefined) {
        if (looksLikeHtml(text, contentType)) {
          throw new BackendError('blocked', {
            retryable: true,
            statusCode: response.status,
          });
        }
        throw new BackendError('invalid_response', { statusCode: response.status });
      }

      const errors = graphqlErrors(data);
      if (errors.length > 0) {
        const error = classifyGraphqlFailure(errors);
        if (error.queryIdFailure) {
          onlyQueryIdFailures = true;
          continue;
        }
        throw error;
      }

      const instructions =
        data?.data?.search_by_raw_query?.search_timeline?.timeline?.instructions;
      if (!Array.isArray(instructions)) {
        throw new BackendError('upstream_changed');
      }
      return {
        tweets: parseTweetsFromInstructions(instructions),
        cursor: extractBottomCursor(instructions),
      };
    }

    if (onlyQueryIdFailures) {
      throw new BackendError('upstream_changed', { queryIdFailure: true });
    }
    throw new BackendError('request_failed');
  }

  async fetchPageWithRefresh(query, count, cursor, refreshState, product) {
    try {
      return await this.fetchPage(query, count, cursor, product);
    } catch (error) {
      if (!(error instanceof BackendError) || !error.queryIdFailure || refreshState.used) {
        throw error;
      }
      refreshState.used = true;
      this.discoveredQueryId = await discoverSearchTimelineQueryId(this.timeoutMs);
      if (!this.discoveredQueryId) {
        throw new BackendError('upstream_changed');
      }
      return this.fetchPage(query, count, cursor, product);
    }
  }

  async search(query, limit, product = 'Latest') {
    const tweets = [];
    const seen = new Set();
    const refreshState = { used: false };
    let cursor;
    let pagesFetched = 0;

    while (tweets.length < limit && pagesFetched < 5) {
      const pageCount = Math.min(20, limit - tweets.length);
      const page = await this.fetchPageWithRefresh(
        query,
        pageCount,
        cursor,
        refreshState,
        product,
      );
      pagesFetched += 1;
      let added = 0;

      for (const tweet of page.tweets) {
        if (seen.has(tweet.id)) {
          continue;
        }
        seen.add(tweet.id);
        tweets.push(tweet);
        added += 1;
        if (tweets.length >= limit) {
          break;
        }
      }

      if (!page.cursor || page.cursor === cursor || page.tweets.length === 0 || added === 0) {
        break;
      }
      cursor = page.cursor;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return tweets;
  }
}
