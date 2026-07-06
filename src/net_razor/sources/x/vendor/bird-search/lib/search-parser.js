function firstText(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function unwrapTweetResult(result) {
  if (!result) {
    return undefined;
  }
  return result.tweet ?? result;
}

function unwrapUserResult(result) {
  if (result?.user) {
    return result.user;
  }
  return result;
}

function extractText(result) {
  const article = result?.article?.article_results?.result ?? result?.article;
  const note = result?.note_tweet?.note_tweet_results?.result;
  return firstText(
    article?.plain_text,
    article?.title,
    note?.text,
    note?.richtext?.text,
    result?.legacy?.full_text,
  );
}

function mapTweetResult(rawResult) {
  const result = unwrapTweetResult(rawResult);
  const userResult = unwrapUserResult(result?.core?.user_results?.result);
  const userLegacy = userResult?.legacy;
  const userCore = userResult?.core;
  const username = firstText(userLegacy?.screen_name, userCore?.screen_name);
  const name = firstText(userLegacy?.name, userCore?.name, username);
  const text = extractText(result);

  if (!result?.rest_id || !username || !text || !result?.legacy?.created_at) {
    return undefined;
  }

  return {
    id: result.rest_id,
    text,
    createdAt: result.legacy.created_at,
    replyCount: result.legacy.reply_count,
    retweetCount: result.legacy.retweet_count,
    likeCount: result.legacy.favorite_count,
    quoteCount: result.legacy.quote_count,
    viewCount: result.views?.count ?? result.legacy.ext_views?.count,
    author: {
      username,
      name: name ?? username,
    },
  };
}

function collectTweetResults(entry) {
  const content = entry?.content;
  const results = [];
  const add = (result) => {
    if (result) {
      results.push(result);
    }
  };

  add(content?.itemContent?.tweet_results?.result);
  add(content?.item?.itemContent?.tweet_results?.result);
  for (const item of content?.items ?? []) {
    add(item?.item?.itemContent?.tweet_results?.result);
    add(item?.itemContent?.tweet_results?.result);
    add(item?.content?.itemContent?.tweet_results?.result);
  }
  return results;
}

export function parseTweetsFromInstructions(instructions) {
  const tweets = [];
  const seen = new Set();

  for (const instruction of instructions ?? []) {
    for (const entry of instruction.entries ?? []) {
      for (const result of collectTweetResults(entry)) {
        const tweet = mapTweetResult(result);
        if (!tweet || seen.has(tweet.id)) {
          continue;
        }
        seen.add(tweet.id);
        tweets.push(tweet);
      }
    }
  }
  return tweets;
}

export function extractBottomCursor(instructions) {
  for (const instruction of instructions ?? []) {
    for (const entry of instruction.entries ?? []) {
      const content = entry?.content;
      if (
        content?.cursorType === 'Bottom'
        && typeof content.value === 'string'
        && content.value
      ) {
        return content.value;
      }
    }
  }
  return undefined;
}
