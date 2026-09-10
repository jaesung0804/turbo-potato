"""Bound JSON bytes before parsing, even when Content-Length is inaccurate."""
from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app, max_upload_bytes):
        self.app, self.max_upload_bytes = app, max_upload_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT'}:
            return await self.app(scope, receive, send)
        lengths = [v for k, v in scope['headers'] if k.lower() == b'content-length']
        binary = '/files/' in scope['path'] and '/snapshots/' not in scope['path']
        bound = self.max_upload_bytes if binary else 1024 * 1024

        async def reject(status, detail):
            await JSONResponse({'detail': detail}, status_code=status,
                headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})(scope, receive, send)

        if not lengths:
            return await reject(411, 'Content-Length is required')
        try:
            if len(lengths) != 1:
                raise ValueError()
            declared = int(lengths[0])
            if declared < 0:
                raise ValueError()
        except ValueError:
            return await reject(400, 'Invalid Content-Length')
        if declared > bound:
            return await reject(413, 'Request too large')
        if binary:
            # File route verifies every streamed byte and SHA-256 without buffering it.
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > bound:
                return await reject(413, 'Request too large')
            if not message.get('more_body', False):
                break
        if len(body) != declared:
            return await reject(400, 'Content-Length mismatch')
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
