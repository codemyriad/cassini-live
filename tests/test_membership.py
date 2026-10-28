"""Room membership, asked of Talk as the requesting user and cached briefly."""

import asyncio

from cassini_live.membership import PolledMembership, RoomMembership


def talk(members, asked):
    class Nextcloud:
        async def set_user(self, user):
            self.user = user

        async def ocs(self, method, path):
            asked.append((self.user, path))
            if self.user not in members:
                raise RuntimeError("404 not a participant")
            return []

    return Nextcloud


async def test_guests_and_missing_rooms_are_refused_without_asking():
    asked = []
    membership = RoomMembership(talk({"alice"}, asked))
    assert not await membership.check("", "room1")
    assert not await membership.check("alice", "")
    assert asked == []


async def test_members_are_cached_for_the_ttl_and_others_asked_every_time():
    asked = []
    membership = RoomMembership(talk({"alice"}, asked))
    assert await membership.check("alice", "room1")
    assert await membership.check("alice", "room1")
    assert asked == [("alice", "/ocs/v2.php/apps/spreed/api/v4/room/room1/participants")]
    assert not await membership.check("bob", "room1")
    assert not await membership.check("bob", "room1")
    assert len(asked) == 3
    # Per room: membership of one says nothing about another.
    assert await membership.check("alice", "room2") and len(asked) == 4


async def test_an_expired_answer_is_asked_again_and_a_refusal_evicts_it():
    asked, members = [], {"alice"}
    membership = RoomMembership(talk(members, asked), ttl=0.0)
    assert await membership.check("alice", "room1")
    members.clear()
    assert not await membership.check("alice", "room1")
    assert ("alice", "room1") not in membership._until
    members.add("alice")
    membership.ttl = 30.0
    assert await membership.check("alice", "room1") and len(asked) == 3
    assert await membership.check("alice", "room1") and len(asked) == 3


async def test_polls_keep_a_refusal_for_the_ttl_per_user_and_room():
    asked = []
    polled = PolledMembership(RoomMembership(talk({"alice"}, asked)))
    assert not await polled.check("bob", "room1")
    assert not await polled.check("bob", "room1")
    assert asked == [("bob", "/ocs/v2.php/apps/spreed/api/v4/room/room1/participants")]
    assert not await polled.check("bob", "room2")
    assert await polled.check("alice", "room1") and await polled.check("alice", "room1")
    assert len(asked) == 3


async def test_a_polled_refusal_is_asked_again_once_it_expires():
    asked, members = [], set()
    polled = PolledMembership(RoomMembership(talk(members, asked)), ttl=0.0)
    assert not await polled.check("bob", "room1")
    # Bob joins the call.
    members.add("bob")
    assert await polled.check("bob", "room1") and len(asked) == 2


async def test_concurrent_polls_share_one_check():
    asked, answer = [], asyncio.Event()

    class Slow:
        async def set_user(self, user):
            self.user = user

        async def ocs(self, method, path):
            asked.append(self.user)
            await answer.wait()
            raise RuntimeError("404 not a participant")

    polled = PolledMembership(RoomMembership(Slow))
    polls = [asyncio.create_task(polled.check("bob", "room1")) for _ in range(3)]
    await asyncio.sleep(0.01)
    # A poll that goes away leaves the answer to the others.
    polls[0].cancel()
    answer.set()
    assert await asyncio.gather(*polls[1:]) == [False, False]
    assert asked == ["bob"] and polls[0].cancelled()
