read_input <- function(path, columns) {
  data <- read.csv(path, colClasses = "character", na.strings = "", check.names = FALSE)
  missing <- setdiff(columns, names(data))
  if (length(missing) > 0L) {
    stop(sprintf("%s is missing columns: %s", path, paste(missing, collapse = ", ")), call. = FALSE)
  }
  if (anyDuplicated(names(data))) {
    stop(sprintf("%s contains duplicate column headers", path), call. = FALSE)
  }
  data
}

nonnegative_number <- function(values, field, allow_missing = FALSE) {
  parsed <- suppressWarnings(as.numeric(values))
  invalid <- !is.finite(parsed) | parsed < 0
  if (allow_missing) {
    invalid <- !is.na(values) & invalid
  }
  if (any(invalid)) {
    stop(sprintf("%s must contain finite, nonnegative numbers", field), call. = FALSE)
  }
  parsed
}

staffing_sensitivity <- function(tickets, plans) {
  tickets$handling_minutes <- nonnegative_number(
    tickets$handling_minutes, "handling_minutes", allow_missing = TRUE
  )
  if ("resolved_at" %in% names(tickets)) {
    tickets <- tickets[!is.na(tickets$resolved_at) & nzchar(trimws(tickets$resolved_at)), , drop = FALSE]
  }
  usable <- !is.na(tickets$handling_minutes) & !is.na(tickets$queue)
  tickets <- tickets[usable & nzchar(trimws(tickets$queue)), , drop = FALSE]
  if (!nrow(tickets)) {
    stop("Sensitivity needs at least one resolved ticket with handling time", call. = FALSE)
  }

  keys <- plans[c("scenario", "queue", "week")]
  if (!nrow(plans) || anyNA(keys) || any(!nzchar(trimws(unlist(keys)))) || anyDuplicated(keys)) {
    stop("Each plan row needs a unique scenario, queue, and week", call. = FALSE)
  }
  plans$predicted_tickets <- nonnegative_number(plans$predicted_tickets, "predicted_tickets")
  plans$scheduled_hours <- nonnegative_number(plans$scheduled_hours, "scheduled_hours")

  queue_minutes <- split(tickets$handling_minutes, tickets$queue)
  assumptions <- c("low", "typical", "high")
  result <- lapply(seq_len(nrow(plans)), function(index) {
    plan <- plans[index, , drop = FALSE]
    queue_history <- queue_minutes[[plan$queue]]
    use_queue_history <- length(queue_history) >= 4L
    sample <- if (use_queue_history) queue_history else tickets$handling_minutes
    minutes <- as.numeric(quantile(sample, probs = c(0.25, 0.50, 0.75), type = 7))
    required <- plan$predicted_tickets * minutes / 60
    shortfall <- pmax(0, required - plan$scheduled_hours)
    data.frame(
      scenario = plan$scenario,
      queue = plan$queue,
      week = plan$week,
      assumption = assumptions,
      source = if (use_queue_history) "queue" else "pooled",
      observations = length(sample),
      handling_minutes = round(minutes, 6),
      predicted_tickets = plan$predicted_tickets,
      scheduled_hours = plan$scheduled_hours,
      required_hours = round(required, 6),
      shortfall_hours = round(shortfall, 6),
      covered = ifelse(shortfall <= 1e-9, "true", "false"),
      stringsAsFactors = FALSE
    )
  })
  do.call(rbind, result)
}

main <- function(args) {
  if (length(args) != 3L) {
    stop("Usage: Rscript staffing_sensitivity.R <tickets.csv> <plan.csv> <sensitivity.csv>", call. = FALSE)
  }
  tickets <- read_input(args[[1]], c("queue", "handling_minutes"))
  plans <- read_input(args[[2]], c("scenario", "queue", "week", "predicted_tickets", "scheduled_hours"))
  result <- staffing_sensitivity(tickets, plans)
  temporary <- tempfile(pattern = ".sensitivity-", tmpdir = dirname(args[[3]]), fileext = ".csv")
  on.exit(unlink(temporary), add = TRUE)
  write.csv(result, temporary, row.names = FALSE, na = "")
  if (!file.rename(temporary, args[[3]])) {
    stop(sprintf("Could not write %s", args[[3]]), call. = FALSE)
  }
}

if (sys.nframe() == 0L) {
  main(commandArgs(trailingOnly = TRUE))
}
