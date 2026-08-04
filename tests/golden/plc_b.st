(* trigger: belt_on_handoff *)
IF handoff_signal THEN
_scratch_latched_belt_on_handoff := TRUE;
END_IF;
belt_b_enable := _scratch_latched_belt_on_handoff;
