from app.state_machine import OrderStateMachine, feed_status_to_state, reset_order_states_for_tests


def test_feed_status_map():
    assert feed_status_to_state("complete", 20, 20) == "TRADED"
    assert feed_status_to_state("complete", 10, 20) == "PART_TRADED"
    assert feed_status_to_state("rejected", 0, 20) == "REJECTED"
    assert feed_status_to_state("open", 0, 20) == "PENDING"


def test_transit_to_traded():
    reset_order_states_for_tests()
    machine = OrderStateMachine("sig")
    machine.transition("TRANSIT")
    machine.transition("TRADED")
    assert machine.is_terminal()
