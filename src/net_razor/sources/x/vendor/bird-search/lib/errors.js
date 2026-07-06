export class BackendError extends Error {
  constructor(type, options = {}) {
    super(type);
    this.name = 'BackendError';
    this.type = type;
    this.retryable = options.retryable === true;
    this.statusCode = Number.isInteger(options.statusCode) ? options.statusCode : undefined;
    this.queryIdFailure = options.queryIdFailure === true;
  }
}

export function errorResponse(error) {
  const backendError = error instanceof BackendError
    ? error
    : new BackendError('request_failed');

  const payload = {
    type: backendError.type,
    retryable: backendError.retryable,
  };
  if (backendError.statusCode !== undefined) {
    payload.status_code = backendError.statusCode;
  }
  return {
    protocol_version: 1,
    ok: false,
    items: [],
    error: payload,
  };
}
