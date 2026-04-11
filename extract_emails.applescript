-- extract_emails.applescript
-- Searches all Mail.app accounts for job-related emails from the last N days
-- Outputs structured text for AI processing
-- Usage: osascript extract_emails.applescript [days_back]

on run argv
	if (count of argv) > 0 then
		set daysBack to (item 1 of argv) as integer
	else
		set daysBack to 7
	end if
	set cutoffDate to (current date) - (daysBack * days)
	set output to ""
	set emailCount to 0

	tell application "Mail"
		repeat with acct in every account
			try
				set acctName to name of acct

				-- Search INBOX
				try
					set inboxMbox to mailbox "INBOX" of acct
					set recentMsgs to (every message of inboxMbox whose date received > cutoffDate)

					repeat with msg in recentMsgs
						try
							set subj to subject of msg

							if subj contains "application" or subj contains "applied" or subj contains "interview" or subj contains "offer" or subj contains "rejected" or subj contains "hiring" or subj contains "position" or subj contains "candidate" or subj contains "recruitment" or subj contains "resume" or subj contains "job" or subj contains "career" or subj contains "opportunity" or subj contains "recruiter" or subj contains "onboarding" or subj contains "background check" then

								set senderAddr to sender of msg
								set dateRecv to date received of msg as string

								-- Get body safely, truncate to 1500 chars
								set bodyContent to ""
								try
									set bodyContent to content of msg
									if (count of bodyContent) > 1500 then
										set bodyContent to text 1 thru 1500 of bodyContent
									end if
								on error
									set bodyContent to "(could not read body)"
								end try

								set output to output & "===EMAIL_START===" & linefeed
								set output to output & "Account: " & acctName & linefeed
								set output to output & "Subject: " & subj & linefeed
								set output to output & "From: " & senderAddr & linefeed
								set output to output & "Date: " & dateRecv & linefeed
								set output to output & "Body: " & bodyContent & linefeed
								set output to output & "===EMAIL_END===" & linefeed & linefeed

								set emailCount to emailCount + 1
							end if

						on error
							-- Skip problematic messages
						end try
					end repeat
				on error
					-- Skip if INBOX not accessible
				end try

				-- Search Sent mailbox — try `sent mailbox` property first, fall back
				-- to scanning mailboxes for one named like "Sent".
				set sentMboxList to {}
				try
					set end of sentMboxList to sent mailbox of acct
				end try
				try
					repeat with mbx in (every mailbox of acct)
						try
							set mbxName to name of mbx
							if mbxName contains "Sent" or mbxName contains "sent" then
								set end of sentMboxList to mbx
							end if
						end try
					end repeat
				end try

				repeat with sentMbox in sentMboxList
					try
						set recentSent to (every message of sentMbox whose date sent > cutoffDate)

						repeat with msg in recentSent
							try
								set subj to subject of msg

								if subj contains "application" or subj contains "applied" or subj contains "interview" or subj contains "offer" or subj contains "rejected" or subj contains "hiring" or subj contains "position" or subj contains "candidate" or subj contains "recruitment" or subj contains "resume" or subj contains "job" or subj contains "career" or subj contains "opportunity" or subj contains "recruiter" or subj contains "onboarding" or subj contains "cover letter" or subj contains "applying" or subj contains "role" or subj contains "follow" then

									set senderAddr to sender of msg
									set recipientList to ""
									try
										repeat with r in to recipients of msg
											set recipientList to recipientList & address of r & ", "
										end repeat
									end try

									-- Prefer date sent for sent messages; fall back to date received
									set dateStr to ""
									try
										set dateStr to (date sent of msg) as string
									on error
										try
											set dateStr to (date received of msg) as string
										end try
									end try

									set bodyContent to ""
									try
										set bodyContent to content of msg
										if (count of bodyContent) > 1500 then
											set bodyContent to text 1 thru 1500 of bodyContent
										end if
									on error
										set bodyContent to "(could not read body)"
									end try

									set output to output & "===EMAIL_START===" & linefeed
									set output to output & "Account: " & acctName & linefeed
									set output to output & "Direction: SENT" & linefeed
									set output to output & "Subject: " & subj & linefeed
									set output to output & "From: " & senderAddr & linefeed
									set output to output & "To: " & recipientList & linefeed
									set output to output & "Date: " & dateStr & linefeed
									set output to output & "Body: " & bodyContent & linefeed
									set output to output & "===EMAIL_END===" & linefeed & linefeed

									set emailCount to emailCount + 1
								end if

							on error
								-- Skip problematic messages
							end try
						end repeat
					on error
						-- Skip if this candidate mailbox isn't accessible
					end try
				end repeat

			on error
				-- Skip problematic accounts
			end try
		end repeat
	end tell

	if emailCount = 0 then
		return "NO_EMAILS_FOUND"
	end if

	return output
end run
