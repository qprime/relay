(* trigger: handoff_on_exit *)
_scratch_edge_handoff_on_exit := sensor_a_exit AND NOT _scratch_prev_handoff_on_exit;
_scratch_prev_handoff_on_exit := sensor_a_exit;
IF _scratch_edge_handoff_on_exit THEN
_scratch_latched_handoff_on_exit := TRUE;
END_IF;
_send_plc_b_handoff_signal := _scratch_latched_handoff_on_exit;
