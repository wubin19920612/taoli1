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


def history(http,route,endpoint,start,end,*,key,limit,cursor=False):
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
            if cursor and (not isinstance(data,dict) or 'list' not in data):
                fail('response_shape_invalid')
            batch=data['list'] if cursor else data
            if not isinstance(batch,list) or len(batch)>limit:
                fail('response_list_invalid')
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
            if 'cursor' not in data or not isinstance(data['cursor'],str):
                fail('cursor_missing_or_invalid')
            following=data['cursor']
            if not batch:
                if following:
                    fail('empty_page_with_cursor')
                break
            # A short page with a cursor still has an explicit continuation.
            if not following:
                if len(batch)==limit:
                    fail('full_page_without_cursor')
                break
            if following in cursors:
                fail('cursor_repeated')
            cursors.add(following)
            params=dict(params,cursor=following)
            context['cursor_used']=True
        left=right+1  # inclusive millisecond windows; no gap or overlap
    return rows
