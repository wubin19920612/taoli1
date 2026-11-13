"""Bounded complete history reads with safe, actionable failure context.

No response bodies, account IDs, raw cursors, URLs or exception text enter the
diagnostic. Limits bound resource use; reaching one is never a complete result.
"""
import time

from .transport import APIError

MAX_PAGES = 1024
MAX_ROWS = 250000
READ_SECONDS = 8
WINDOW_MS = 30*86400000
# Both observed on the production UTA account (2026-11-13) for empty windows.
NULL_TERMINAL_ENDPOINTS = frozenset(('/api/v3/account/financial-records',
                                    '/api/v3/position/history-position'))


def page_rows(data, endpoint, *, cursor, limit):
    """Parse successful Transport data; do not turn malformed data into zero.

    UTA empty arrays with explicit null cursors are observed terminal pages.
    Only financial-records and history-position have the observed null LIST/null
    cursor exception.
    Transport must validate the outer success code BEFORE returning this data.
    """
    if cursor and (not isinstance(data,dict) or 'list' not in data):
        raise ValueError('response_shape_invalid')
    batch=data['list'] if cursor else data
    if (cursor and endpoint in NULL_TERMINAL_ENDPOINTS and batch is None
            and 'cursor' in data and data['cursor'] is None):
        return [], ''
    if not isinstance(batch,list) or len(batch)>limit:
        raise ValueError('response_list_invalid')
    if not cursor:
        return batch, None
    if not batch and 'cursor' in data and data['cursor'] is None:
        return batch, ''
    if 'cursor' not in data or not isinstance(data['cursor'],str):
        raise ValueError('cursor_missing_or_invalid')
    following=data['cursor']
    if not batch and following:
        raise ValueError('empty_page_with_cursor')
    if len(batch)==limit and not following:
        raise ValueError('full_page_without_cursor')
    return batch, following


class HistoryError(APIError):
    def __init__(self, diagnostic):
        super().__init__('invalid_response')
        self.diagnostic=diagnostic


def error_details(error):
    result=dict(error_kind=error.kind if isinstance(error,APIError) else 'unknown')
    if isinstance(error,APIError):
        if error.http_status is not None:
            result['http_status']=error.http_status
        if error.api_code is not None:
            result['api_code']=error.api_code
    return result


def history(http,route,endpoint,start,end,*,key,limit,cursor=False,filters=None):
    context=dict(route=route,endpoint=endpoint,from_ms=start,to_ms=end,page=0,rows_read=0)
    def fail(condition,**extra):
        raise HistoryError(dict(context,condition=condition,**extra)) from None
    if type(start) is not int or type(end) is not int or not 0<=start<=end:
        fail('invalid_window')
    rows,seen=[],set()
    deadline=time.monotonic()+READ_SECONDS
    requests=0
    left=start
    while left<=end:
        right=min(end,left+WINDOW_MS)
        context.update(from_ms=left,to_ms=right,page=0,cursor_used=False)
        params=(dict(category='USDT-FUTURES',startTime=str(left),endTime=str(right),limit=limit)
                if cursor else {'from':left,'to':right,'limit':limit})
        params.update(filters or {})
        cursors=set()
        while True:
            context['page']+=1
            if requests>=MAX_PAGES:
                fail('page_budget_exhausted')
            if time.monotonic()>=deadline:
                fail('read_deadline_exceeded')
            requests+=1
            if not cursor:
                params=dict(params,page=context['page'])
            try:
                data=http.request('GET',endpoint,dict(params),group='account',deadline=deadline)
            except Exception as error:
                fail('request_failed',**error_details(error))
            try:
                batch,following=page_rows(data,endpoint,cursor=cursor,limit=limit)
            except ValueError as error:
                fail(str(error))
            for row in batch:
                try:
                    identity=key(row)
                except (KeyError,TypeError):
                    fail('row_identity_missing')
                parts=identity if isinstance(identity,tuple) else (identity,)
                if not all(isinstance(v,str) and v for v in parts):
                    fail('row_identity_invalid')
                if identity in seen:
                    fail('duplicate_id')
                seen.add(identity)
            rows.extend(batch)
            context['rows_read']=len(rows)
            if len(rows)>MAX_ROWS:
                fail('row_budget_exhausted')
            if not cursor:
                if len(batch)<limit:
                    break
                continue
            if not batch:
                break
            # A short page with a cursor still has an explicit continuation.
            if not following:
                break
            if following in cursors:
                fail('cursor_repeated')
            cursors.add(following)
            params=dict(params,cursor=following)
            context['cursor_used']=True
        left=right+1  # inclusive millisecond windows; no gap or overlap
    return rows
