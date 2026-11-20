import pytest
from media_cli.media.aniskip import validated_skip_intervals
from media_cli.media.provider_skips import playback_skip_intervals
from media_cli.playback.skip_lookup import SkipLookup


def interval(*, source=None, end=1430.07, start=1340.48, kind='ed', reference=None):
    item={'type':kind,'start':start,'end':end}
    if source:item['source']=source
    else:item['episode_length']=reference or end
    return item


@pytest.mark.parametrize('overrun',[0.07, 1, 2])
@pytest.mark.parametrize('source',[None,'provider'])
def test_small_ending_overrun_clamped_and_precision_preserved(source,overrun):
    original=interval(source=source,end=1430+overrun)
    result=playback_skip_intervals([original],1430)
    assert len(result)==1
    assert result[0]['end']==1430
    assert result[0]['start']==1340.48
    assert original['end']==1430+overrun


@pytest.mark.parametrize('source',[None,'provider'])
@pytest.mark.parametrize('changes',[{'end':1432.001},{'start':1430,'end':1431},
                                     {'kind':'op'},{'reference':1500}])
def test_invalid_or_large_overruns_still_rejected(source,changes):
    if source and 'reference' in changes:
        pytest.skip('Provider ranges have no reference runtime')
    assert playback_skip_intervals([interval(source=source,**changes)],1430)==[]


def test_existing_precise_range_not_rounded():
    item=interval(end=1429.75)
    assert validated_skip_intervals([item],1430)==[item]


def test_provider_clamped_ending_still_prevents_aniskip_fill():
    native=interval(source='provider')
    other=interval(kind='op',start=5,end=90,reference=1430)
    result=playback_skip_intervals([native,other],1430)
    assert len(result)==1 and result[0]['type']=='ed'
    assert result[0]['source']=='provider'


def test_repeated_duration_poll_keeps_clamped_interval_without_network():
    lookup=SkipLookup()
    lookup.reset(None,1,[interval()])
    try:
        first=lookup.poll(1430)
        assert first and first[0]['end']==1430
        assert lookup.poll(1430)==first
    finally:lookup.close()
