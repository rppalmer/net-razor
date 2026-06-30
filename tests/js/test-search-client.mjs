import assert from 'node:assert/strict';

import { BackendError } from '../../platforms/x-api/src/x_api/vendor/bird-search/lib/errors.js';
import { SearchClient } from '../../platforms/x-api/src/x_api/vendor/bird-search/lib/search-client.js';

function tweetResult(id) {
  return {
    rest_id: id,
    core: {
      user_results: {
        result: {
          core: { screen_name: 'example_user', name: 'Example User' },
          legacy: { screen_name: 'example_user', name: 'Example User' },
        },
      },
    },
    legacy: {
      created_at: 'Wed May 20 14:30:00 +0000 2026',
      full_text: `Post ${id}`,
      reply_count: 0,
      retweet_count: 0,
      favorite_count: 0,
      quote_count: 0,
    },
  };
}

function timelineResponse(id, cursor) {
  const entries = [
    {
      content: {
        itemContent: {
          tweet_results: { result: tweetResult(id) },
        },
      },
    },
  ];
  if (cursor) {
    entries.push({ content: { cursorType: 'Bottom', value: cursor } });
  }
  return {
    data: {
      search_by_raw_query: {
        search_timeline: {
          timeline: {
            instructions: [{ entries }],
          },
        },
      },
    },
  };
}

async function testEmptyRateLimitResponse() {
  globalThis.fetch = async () => new Response('', { status: 429 });
  const client = new SearchClient({
    authToken: 'auth',
    ct0: 'csrf',
    timeoutMs: 1000,
  });

  await assert.rejects(
    () => client.search('python', 1),
    (error) => (
      error instanceof BackendError
      && error.type === 'rate_limited'
      && error.retryable === true
    ),
  );
}

async function testQueryIdRefreshAndPagination() {
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    const requestUrl = String(url);

    if (requestUrl.startsWith('https://x.com/i/api/graphql/')) {
      const parsedUrl = new URL(requestUrl);
      const queryId = parsedUrl.pathname.split('/').at(-2);
      if (queryId !== 'freshSearchId') {
        return new Response(
          JSON.stringify({
            errors: [{
              message: 'Validation failed',
              extensions: { code: 'GRAPHQL_VALIDATION_FAILED' },
            }],
          }),
          { status: 400, headers: { 'content-type': 'application/json' } },
        );
      }

      const variables = JSON.parse(parsedUrl.searchParams.get('variables'));
      const response = variables.cursor
        ? timelineResponse('2')
        : timelineResponse('1', 'next-page');
      return new Response(
        JSON.stringify(response),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    }

    if (requestUrl === 'https://x.com/?lang=en' || requestUrl === 'https://x.com/explore') {
      return new Response(
        '<script src="https://abs.twimg.com/responsive-web/client-web/bundle.js"></script>',
        { status: 200 },
      );
    }

    if (requestUrl.endsWith('/bundle.js')) {
      return new Response(
        'e.exports={queryId:"freshSearchId",operationName:"SearchTimeline"}',
        { status: 200 },
      );
    }

    throw new Error(`Unexpected URL: ${requestUrl}`);
  };

  const client = new SearchClient({
    authToken: 'auth',
    ct0: 'csrf',
    timeoutMs: 1000,
  });
  const tweets = await client.search('python lang:en', 2);

  assert.deepEqual(tweets.map((tweet) => tweet.id), ['1', '2']);
  const successfulSearches = requests.filter(
    (request) => request.url.includes('/freshSearchId/SearchTimeline'),
  );
  assert.equal(successfulSearches.length, 2);
  for (const request of successfulSearches) {
    assert.equal(request.options.method, 'POST');
    const variables = JSON.parse(new URL(request.url).searchParams.get('variables'));
    assert.equal(variables.product, 'Latest');
    assert.equal(variables.rawQuery, 'python lang:en');
  }
}

await testEmptyRateLimitResponse();
await testQueryIdRefreshAndPagination();
