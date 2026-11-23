source("presales/staffing_sensitivity.R")

tickets <- data.frame(
  queue = c(rep("Network", 5L), "Identity"),
  handling_minutes = c("15", "30", "45", "60", "999", "5"),
  resolved_at = c(rep("2025-01-01T13:00:00Z", 4L), NA, "2025-01-01T13:00:00Z")
)
plans <- data.frame(
  scenario = c("Constrained", "Expanded", "Expanded"),
  queue = c("Network", "Network", "Identity"),
  week = "2025-01-06",
  predicted_tickets = c("12", "12", "0"),
  scheduled_hours = c("7.5", "12", "0")
)

rows <- staffing_sensitivity(tickets, plans)
stopifnot(
  identical(rows$handling_minutes[1:3], c(26.25, 37.5, 48.75)),
  identical(rows$required_hours[1:3], c(5.25, 7.5, 9.75)),
  identical(rows$shortfall_hours[1:3], c(0, 0, 2.25)),
  identical(rows$covered[1:3], c("true", "true", "false")),
  all(rows$covered[4:9] == "true"),
  all(rows$source[1:6] == "queue"),
  all(rows$source[7:9] == "pooled"),
  all(rows$observations[7:9] == 5)
)

duplicate_plans <- rbind(plans, plans[1L, ])
error <- tryCatch(staffing_sensitivity(tickets, duplicate_plans), error = identity)
stopifnot(inherits(error, "error"), grepl("unique scenario", conditionMessage(error)))

cat("Staffing sensitivity checks passed\n")
