import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  extractBottomCursor,
  parseTweetsFromInstructions,
} from '../../platforms/x-api/src/x_api/vendor/bird-search/lib/search-parser.js';

const fixtureUrl = new URL('../fixtures/search_timeline.json', import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, 'utf8'));
const instructions =
  fixture.data.search_by_raw_query.search_timeline.timeline.instructions;
const tweets = parseTweetsFromInstructions(instructions);

assert.equal(tweets.length, 1);
assert.equal(tweets[0].id, '1234567890');
assert.equal(tweets[0].text, 'A sanitized fixture post');
assert.equal(tweets[0].author.username, 'example_user');
assert.equal(tweets[0].quoteCount, 1);
assert.equal(tweets[0].viewCount, '99');
assert.equal(extractBottomCursor(instructions), 'sanitized-cursor');
