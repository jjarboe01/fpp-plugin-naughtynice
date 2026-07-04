<?php
# $menuEntries is an array of menu entries for this plugin.
# See FalconChristmas/fpp-plugin-Template for the field reference this
# follows (text/type/page/wrap).

$menuEntries = Array(
	Array(
		'text' => 'NaughtyNice Cloud - Status',
		'type' => 'status',
		'page' => 'status.php',
		'wrap' => 1
	),
	Array(
		'text' => 'NaughtyNice Cloud - Setup',
		'type' => 'content',
		'page' => 'content.php',
		'wrap' => 1
	),
	Array(
		'text' => 'NaughtyNice Cloud - About',
		'type' => 'help',
		'page' => 'about.php',
		'wrap' => 1
	),
	Array(
		'text' => 'NaughtyNice Cloud - Home',
		'type' => 'help',
		'page' => 'https://github.com/jjarboe01/fpp-plugin-naughtynice',
		'wrap' => 1
	)
);

foreach ($menuEntries as $entry)
{
	if ($entry['type'] != $menu)
		continue;

	if (preg_match('/^http.?:\/\//', $entry['page']))
	{
		printf("<li><a href='%s' target='_blank'>%s</a></li>\n",
			$entry['page'], $entry['text']);
	}
	else
	{
		$nopage = '';
		if (isset($entry['wrap']) && ($entry['wrap'] == 0))
			$nopage = '&nopage=1';

		printf("<li><a href='plugin.php?plugin=%s&page=%s%s'>%s</a></li>\n",
			$plugin, $entry['page'], $nopage, $entry['text']);
	}
}
?>
