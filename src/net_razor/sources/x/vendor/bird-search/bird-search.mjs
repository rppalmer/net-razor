#!/usr/bin/env node

import { errorResponse, BackendError } from './lib/errors.js';
import { SearchClient } from './lib/search-client.js';

const MAX_INPUT_BYTES = 16 * 1024;

function writeResponse(response) {
  process.stdout.write(JSON.stringify(response));
}

async function readRequest() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) {
      throw new BackendError('invalid_response');
    }
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new BackendError('invalid_response');
  }
}

function readCredentials() {
  const authToken = process.env.AUTH_TOKEN?.trim();
  const ct0 = process.env.CT0?.trim();
  if (!authToken || !ct0) {
    throw new BackendError('not_configured');
  }
  return { authToken, ct0 };
}

function validateSearchRequest(request) {
  if (request?.protocol_version !== 1 || request?.action !== 'search') {
    throw new BackendError('invalid_response');
  }
  if (typeof request.query !== 'string' || !request.query.trim() || request.query.length > 600) {
    throw new BackendError('invalid_response');
  }
  if (!Number.isInteger(request.count) || request.count < 1 || request.count > 50) {
    throw new BackendError('invalid_response');
  }
  if (!['Latest', 'Top'].includes(request.product)) {
    throw new BackendError('invalid_response');
  }
  if (
    !Number.isInteger(request.upstream_timeout_ms)
    || request.upstream_timeout_ms < 1000
    || request.upstream_timeout_ms > 120000
  ) {
    throw new BackendError('invalid_response');
  }
}

async function main() {
  const request = await readRequest();
  if (request?.protocol_version === 1 && request?.action === 'check') {
    return {
      protocol_version: 1,
      ok: true,
      backend: 'SearchTimeline',
      read_only: true,
    };
  }

  validateSearchRequest(request);
  const credentials = readCredentials();
  const client = new SearchClient({
    ...credentials,
    timeoutMs: request.upstream_timeout_ms,
  });
  const items = await client.search(request.query.trim(), request.count, request.product);
  return {
    protocol_version: 1,
    ok: true,
    items,
    error: null,
  };
}

try {
  writeResponse(await main());
} catch (error) {
  writeResponse(errorResponse(error));
}
